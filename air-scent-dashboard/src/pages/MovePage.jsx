import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import {
  Crosshair,
  Bot,
  LocateFixed,
  Wind,
  Check,
  ChevronLeft,
  ChevronRight,
  X,
  Square,
  Sparkles,
  Maximize2,
  Minimize2,
} from "lucide-react";

import {
  DEFAULT_WARDS,
  MAX_WARDS,
  loadWards,
  loadWardScents,
  getWardScent,
  saveWards,
  wardDisplayName,
} from "../utils/wardStorage";
import useActiveRobotMap from "../hooks/useActiveRobotMap";
import useGdmRobotPose from "../hooks/useGdmRobotPose";
import {
  GDM_FLOORPLAN_URL,
  computeContainLayout,
  containerPointToMapPercent,
  displayYawCss,
  fetchGdmRooms,
  mapPercentToContainer,
  mapPercentToWorldMeters,
  poseToMapPercent,
  roomsToWards,
  screenDragToMapYaw,
  sendInitialPose,
  sendNavGoal,
  cancelNavGoal,
  yawFromRobotToMapPercent,
  yawToDisplayDeg,
  fetchPatrolZones,
  orientedRectToMapPolygon,
} from "../services/gdmMap";
import { boundaryFitView } from "../utils/boundaryView";
import { getScentVisual, readingForWard, isKitchenWard, applyWardJudgment } from "../utils/scentVisual";
import useMoveUiSync from "../hooks/useMoveUiSync";
import { fetchMoveUi, putMoveUi, stopSharedFragranceSpray } from "../services/moveUi";
import { announceMoveTo, announceOdorDepart, announceScentConfigured, announceScentMission } from "../utils/robotAnnounce";
import AiRecommendPage from "./AiRecommendPage";
import "../components/MapScentPopover.css";
import MapZoneOverlay from "../components/MapZoneOverlay";
import RobotLaunchChip from "../components/RobotLaunchChip";
import LocMatchChip from "../components/LocMatchChip";
import CleanMapLayer from "../components/CleanMapLayer";
import useWardJudgment from "../hooks/useWardJudgment";
import {
  captureScentReturnPose,
  clearScentReturn,
} from "../utils/scentReturn";
import {
  loadDeveloperMode,
  subscribeDeveloperMode,
} from "../utils/developerMode";
import "./MovePage.css";

const MAP_MORPH_MS = 520;
const MAP_MORPH_EASE = "cubic-bezier(0.16, 1, 0.3, 1)";
const MAP_STAGE_RADIUS = 16;

function plainRect(rect) {
  return {
    left: rect.left,
    top: rect.top,
    width: rect.width,
    height: rect.height,
  };
}

function elementScale(el) {
  const transform = window.getComputedStyle(el).transform;
  if (!transform || transform === "none") return { x: 1, y: 1 };
  const matrix = new DOMMatrix(transform);
  return {
    x: Math.hypot(matrix.a, matrix.b) || 1,
    y: Math.hypot(matrix.c, matrix.d) || 1,
  };
}

function captureStageSnap(el) {
  const scale = elementScale(el);
  const radius = Number.parseFloat(window.getComputedStyle(el).borderTopLeftRadius) || 0;
  return {
    rect: plainRect(el.getBoundingClientRect()),
    radius: radius * Math.min(scale.x, scale.y),
  };
}

function morphTransform(layout, visual) {
  const sx = visual.width / Math.max(layout.width, 1);
  const sy = visual.height / Math.max(layout.height, 1);
  const dx = visual.left - layout.left;
  const dy = visual.top - layout.top;
  return { sx, sy, dx, dy };
}

function clearStageMorph(el) {
  if (!el) return;
  el.style.transition = "";
  el.style.transform = "";
  el.style.transformOrigin = "";
  el.style.borderRadius = "";
  el.style.willChange = "";
}

function runStageMorph(el, fromRect, toRect, fromRadius, toRadius, onDone) {
  el.style.transition = "none";
  el.style.transform = "none";
  el.style.borderRadius = "";
  const layout = plainRect(el.getBoundingClientRect());
  const destination = toRect ?? layout;
  const from = morphTransform(layout, fromRect);
  const to = morphTransform(layout, destination);
  const fromScale = Math.min(Math.abs(from.sx), Math.abs(from.sy)) || 1;
  const toScale = Math.min(Math.abs(to.sx), Math.abs(to.sy)) || 1;

  const write = (pose, radius) => {
    el.style.transformOrigin = "0 0";
    el.style.transform = `translate(${pose.dx}px, ${pose.dy}px) scale(${pose.sx}, ${pose.sy})`;
    el.style.borderRadius = `${radius}px`;
  };

  el.style.willChange = "transform, border-radius";
  write(from, fromRadius / fromScale);
  void el.offsetWidth;
  el.style.transition = `transform ${MAP_MORPH_MS}ms ${MAP_MORPH_EASE}, border-radius ${MAP_MORPH_MS}ms ${MAP_MORPH_EASE}`;
  write(to, toRadius / toScale);

  let settled = false;
  const finish = () => {
    if (settled) return;
    settled = true;
    el.removeEventListener("transitionend", onEnd);
    window.clearTimeout(timer);
    onDone?.();
  };
  const onEnd = (event) => {
    if (event.target !== el || event.propertyName !== "transform") return;
    finish();
  };
  el.addEventListener("transitionend", onEnd);
  const timer = window.setTimeout(finish, MAP_MORPH_MS + 90);
  return () => {
    settled = true;
    el.removeEventListener("transitionend", onEnd);
    window.clearTimeout(timer);
  };
}

const GDM_SYNC_KEY = "air-scent-wards-gdm-synced";
// 위치 추정 화살표 전체 길이(px, 머리 포함). 약 2cm.
const POSE_ARROW_PX = 76;

const SCENT_WAVE_DELAYS = [0];

function scentSpreadRadius(cx, cy, roomClip, mapLayout) {
  let far = 0;
  if (roomClip?.svg) {
    for (const pair of roomClip.svg.trim().split(/\s+/)) {
      const [x, y] = pair.split(",").map(Number);
      if (!Number.isFinite(x) || !Number.isFinite(y)) continue;
      far = Math.max(far, Math.hypot(x - cx, y - cy));
    }
  }
  if (far <= 0) {
    far = Math.max(mapLayout.width, mapLayout.height) * 0.46;
  }
  return far * 1.7;
}

function pointInPolygon(x, y, polygon) {
  let inside = false;
  for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i, i += 1) {
    const [xi, yi] = polygon[i];
    const [xj, yj] = polygon[j];
    const crosses = yi > y !== yj > y;
    if (!crosses) continue;
    const xAtY = ((xj - xi) * (y - yi)) / (yj - yi) + xi;
    if (x < xAtY) inside = !inside;
  }
  return inside;
}

function closestPointOnSegment(px, py, ax, ay, bx, by) {
  const dx = bx - ax;
  const dy = by - ay;
  const lengthSq = dx * dx + dy * dy;
  if (lengthSq === 0) return { x: ax, y: ay };
  const t = Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / lengthSq));
  return { x: ax + t * dx, y: ay + t * dy };
}

/** 방 밖이면 가장 가까운 가장자리로 붙인다. 방이 없으면 그대로. */
function clampToRoom(x, y, polygon) {
  if (!polygon || polygon.length < 3) return { x, y };
  if (pointInPolygon(x, y, polygon)) return { x, y };
  let best = null;
  let bestDistance = Infinity;
  for (let index = 0; index < polygon.length; index += 1) {
    const [ax, ay] = polygon[index];
    const [bx, by] = polygon[(index + 1) % polygon.length];
    const point = closestPointOnSegment(x, y, ax, ay, bx, by);
    const distance = (point.x - x) ** 2 + (point.y - y) ** 2;
    if (distance < bestDistance) {
      best = point;
      bestDistance = distance;
    }
  }
  return best ?? { x, y };
}
const WARD_BATT_LOW_V = 3.6;
const WARD_CHIP_LABEL = {
  Peppermint: "페퍼민트",
  Lavender: "라벤더",
  Orange: "오렌지",
  Vinegar: "식초",
  Unknown: "모르는 냄새",
};
const WARD_CHIP_STATE = {
  WARMUP: "예열",
  CLEAN: "무향",
  SCENT: "향기",
  MALODOR: "악취",
  UNKNOWN: "모르는 냄새",
  HOLD: "보류",
};

function isOdorDepart(mqttAirQuality, wardJudgment) {
  const state = String(wardJudgment?.state || "");
  const judgmentLabel = String(wardJudgment?.label || "");
  if (
    state === "MALODOR" ||
    state === "UNKNOWN" ||
    judgmentLabel === "Vinegar" ||
    judgmentLabel === "Unknown"
  ) {
    return true;
  }
  const tone = String(mqttAirQuality?.tone || "");
  const label = String(mqttAirQuality?.label || "");
  return tone === "odor" || /odor|악취|vinegar|식초/i.test(label);
}

/** 핀 위 현재 향기 글자색. 이름표(페퍼민트·라벤더·오렌지)와 같은 톤. */
function liveChipTone(state, label) {
  if (state === "MALODOR" || state === "UNKNOWN" || label === "Vinegar" || label === "Unknown") return "odor";
  if (state === "CLEAN") return "fresh";
  if (label === "Peppermint") return "mint";
  if (label === "Lavender") return "lavender";
  if (label === "Orange") return "orange";
  if (state === "SCENT") return "other";
  return null;
}

/** 와드 핀 위 한 줄. 배터리 점 + 이름·세기만. 판정이 없으면 null. */
function wardLiveChip(entry) {
  if (!entry) return null;
  const state = entry.state;
  const name =
    state === "WARMUP"
      ? "예열"
      : WARD_CHIP_LABEL[entry.label] || WARD_CHIP_STATE[state] || null;
  const pct = Number(entry.strength_pct);
  const showPct = state !== "WARMUP" && state !== "CLEAN" && Number.isFinite(pct);
  const volts = Number(entry.batt_v);
  const batt = Number.isFinite(volts) && volts >= 0 ? (volts < WARD_BATT_LOW_V ? "low" : "ok") : null;
  if (!name && !batt) return null;
  return {
    name,
    tone: liveChipTone(state, entry.label),
    pct: showPct ? Math.round(Math.max(0, Math.min(100, pct))) : null,
    batt,
    volts: batt ? volts : null,
  };
}

const VISUAL_CHIP_NAME = {
  mint: "페퍼민트",
  lavender: "라벤더",
  orange: "오렌지",
  odor: "악취",
  fresh: "무향",
  other: "향",
};

/** 와드 판정이 없을 때 MQTT 신뢰도·세기를 같은 줄에 쓴다. */
function visualLiveChip(visual) {
  if (!visual || visual.tone === "offline" || visual.tone === "waiting") return null;
  const fresh = visual.tone === "fresh";
  const parsed = Number(String(visual.confidence ?? "").replace("%", ""));
  const fromIntensity = typeof visual.intensity === "number" ? Math.round(visual.intensity * 100) : null;
  const pct = fresh
    ? null
    : Number.isFinite(parsed)
      ? Math.round(Math.max(0, Math.min(100, parsed)))
      : fromIntensity;
  const name = VISUAL_CHIP_NAME[visual.tone] || visual.label || null;
  if (!name && pct == null) return null;
  return { name, pct, batt: null, volts: null, tone: visual.tone || null };
}

function chipWithPercent(chip, visual) {
  if (chip?.tone === "fresh" || chip?.name === "무향") return { ...chip, pct: null };
  if (chip?.pct != null) return chip;
  const extra = visualLiveChip(visual);
  if (!chip) return extra;
  if (extra?.pct == null || extra.tone === "fresh") return chip;
  return { ...chip, pct: extra.pct, name: chip.name || extra.name };
}

const ROOM_BASE = "#d1fae5";
const ROOM_BASE_STROKE = "#86efac";
const ROOM_SMELL = {
  mint: { fill: "#0d9488", stroke: "#0f766e" },
  lavender: { fill: "#7c3aed", stroke: "#5b21b6" },
  orange: { fill: "#eab308", stroke: "#a16207" },
  odor: { fill: "#c2410c", stroke: "#9a3412" },
  other: { fill: "#0f766e", stroke: "#115e59" },
};

function waveFill(visual) {
  if (!visual || visual.tone === "waiting" || visual.tone === "offline") return null;
  if (ROOM_SMELL[visual.tone]) return ROOM_SMELL[visual.tone].fill;
  return null;
}

function hexRgb(hex) {
  const value = String(hex).replace("#", "");
  return [
    parseInt(value.slice(0, 2), 16),
    parseInt(value.slice(2, 4), 16),
    parseInt(value.slice(4, 6), 16),
  ];
}

function mixHex(from, to, amount) {
  const a = hexRgb(from);
  const b = hexRgb(to);
  const t = Math.max(0, Math.min(1, amount));
  return `#${a
    .map((channel, index) => Math.round(channel + (b[index] - channel) * t).toString(16).padStart(2, "0"))
    .join("")}`;
}

function hexAlpha(hex, alpha) {
  const [r, g, b] = hexRgb(hex);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

/** 와드 세기(intensity)가 있으면 그걸, 없으면 화면에 나온 MQTT 퍼센트를 0~1로 쓴다. */
function visualAmount(visual) {
  if (typeof visual?.intensity === "number" && Number.isFinite(visual.intensity)) {
    return Math.max(0, Math.min(1, visual.intensity));
  }
  const parsed = Number(String(visual?.confidence ?? "").replace("%", ""));
  if (Number.isFinite(parsed)) return Math.max(0, Math.min(1, parsed / 100));
  return 0.35;
}

/** 낮은 퍼센트는 거의 물처럼 옅고, 90% 근처는 원색에 가깝게 벌린다. */
function colorStrength(amount) {
  const t = Math.max(0, Math.min(1, amount));
  return Math.pow(t, 1.55);
}

/** 냄새 없음은 연한 초록. 냄새가 있으면 초록과 섞지 않고 그 색만, 세기에 따라 진해진다. */
function paintRoom(visual) {
  const smell = ROOM_SMELL[visual?.tone];
  if (!smell) {
    return {
      fill: hexAlpha(ROOM_BASE, 0.42),
      stroke: ROOM_BASE_STROKE,
      badgeColor: "#166534",
      badgeBg: hexAlpha("#f0fdf4", 0.96),
      badgeBorder: hexAlpha(ROOM_BASE_STROKE, 0.8),
    };
  }
  const t = colorStrength(visualAmount(visual));
  const tinted = mixHex("#f8fafc", smell.fill, 0.22 + 0.78 * t);
  return {
    fill: hexAlpha(tinted, 0.12 + 0.74 * t),
    stroke: mixHex("#e2e8f0", smell.stroke, 0.25 + 0.75 * t),
    badgeColor: smell.stroke,
    badgeBg: hexAlpha(mixHex("#ffffff", smell.fill, 0.08 + 0.2 * t), 0.96),
    badgeBorder: hexAlpha(smell.stroke, 0.28 + 0.5 * t),
  };
}

function MovePage({
  data,
  mqttAirQuality,
  fragranceOn = false,
  fragranceDiffusing = false,
  fragranceSending = false,
  onStopFragrance,
  recommendedFragrance,
  recommendedReason,
  fragranceCartridgeRemaining,
  lastDispenseAt,
  onApplyFragrance,
}) {
  const mapRef = useRef(null);
  const imgRef = useRef(null);
  const stageRef = useRef(null);
  const mapAnimatingRef = useRef(false);
  const [mapExpanded, setMapExpanded] = useState(false);
  const [stageFixed, setStageFixed] = useState(false);
  const [morphSnap, setMorphSnap] = useState(null);

  const toggleMapExpanded = () => {
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const next = !mapExpanded;
    if (reduce) {
      setMorphSnap(null);
      setMapExpanded(next);
      setStageFixed(next);
      return;
    }
    if (stageRef.current) setMorphSnap(captureStageSnap(stageRef.current));
    setMapExpanded(next);
    if (next) setStageFixed(true);
  };

  useLayoutEffect(() => {
    document.body.classList.toggle("map-expanded", mapExpanded);
    return () => document.body.classList.remove("map-expanded");
  }, [mapExpanded]);

  useLayoutEffect(() => {
    const el = stageRef.current;
    if (!el) return undefined;

    if (!stageFixed) {
      clearStageMorph(el);
      mapAnimatingRef.current = false;
      return undefined;
    }

    if (!morphSnap) return undefined;

    if (!mapExpanded) {
      const page = el.closest(".move-page");
      const box = page?.getBoundingClientRect();
      if (!box) return undefined;
      mapAnimatingRef.current = true;
      const cancel = runStageMorph(
        el,
        morphSnap.rect,
        plainRect(box),
        morphSnap.radius,
        MAP_STAGE_RADIUS,
        () => setStageFixed(false)
      );
      return () => {
        cancel();
        mapAnimatingRef.current = false;
      };
    }

    mapAnimatingRef.current = true;
    const cancel = runStageMorph(
      el,
      morphSnap.rect,
      null,
      morphSnap.radius,
      0,
      () => {
        clearStageMorph(el);
        mapAnimatingRef.current = false;
      }
    );
    return () => {
      cancel();
      mapAnimatingRef.current = false;
    };
  }, [mapExpanded, morphSnap, stageFixed]);
  const [wards, setWards] = useState(loadWards);
  const [savedWards, setSavedWards] = useState(loadWards);
  const [selectedId, setSelectedId] = useState(
    () => loadWards()[0]?.id ?? DEFAULT_WARDS[0]?.id ?? null
  );
  const [scentPanelWardId, setScentPanelWardId] = useState(null);
  const [scentPanelView, setScentPanelView] = useState("buttons");
  const [aiChatOpen, setAiChatOpen] = useState(false);
  const [aiChatLive, setAiChatLive] = useState(false);
  const [developerMode, setDeveloperMode] = useState(loadDeveloperMode);
  const [devPrompt, setDevPrompt] = useState("");
  const [devSeed, setDevSeed] = useState("");
  const [mode, setMode] = useState("select");
  const [mapReady, setMapReady] = useState(false);
  const [mapFailed, setMapFailed] = useState(false);
  const [mapLayout, setMapLayout] = useState(null);
  const [moveStatus, setMoveStatus] = useState("대기");
  const [isMoving, setIsMoving] = useState(false);
  const [isNavigating, setIsNavigating] = useState(false);
  const [isStopping, setIsStopping] = useState(false);
  const [sharedSpraying, setSharedSpraying] = useState(false);
  const [roomPolys, setRoomPolys] = useState([]);
  const [, setGdmImporting] = useState(false);
  const [draggingId, setDraggingId] = useState(null);
  const dragRef = useRef(null);
  const [mapMeta, setMapMeta] = useState(null);
  const robotMap = useActiveRobotMap();
  const robotMapRef = useRef(robotMap);
  robotMapRef.current = robotMap;
  const [dropPin, setDropPin] = useState(null);
  const [destinationCollapsed, setDestinationCollapsed] = useState(false);
  const [destinationHeight, setDestinationHeight] = useState(null);
  const [destinationDockBottom, setDestinationDockBottom] = useState(0);
  const collapseDestination = (event) => {
    const node = event.currentTarget.closest(".slam-destination");
    if (node) {
      const rect = node.getBoundingClientRect();
      setDestinationHeight(rect.height);
      setDestinationDockBottom(Math.max(0, Math.round(window.innerHeight - rect.bottom)));
    }
    setDestinationCollapsed(true);
  };
  const expandDestination = () => {
    setDestinationCollapsed(false);
  };
  const [navGoal, setNavGoal] = useState(null);
  const [poseDragging, setPoseDragging] = useState(false);
  // 와드(목표 표시) 끌어 옮기기 — 화면 상태에만 저장, ROS 에는 안 보냄
  const [goalDragging, setGoalDragging] = useState(false);
  const [flashError, setFlashError] = useState(null);
  const goalDragRef = useRef(null);
  const [poseEstimating, setPoseEstimating] = useState(false);
  const [locStatus, setLocStatus] = useState(null);
  const [missionPhase, setMissionPhase] = useState(null);
  const [manualControl, setManualControl] = useState(false);
  const [missionReady, setMissionReady] = useState(null);
  const [lastEvent, setLastEvent] = useState(null);
  const [localizationState, setLocalizationState] = useState(null);
  // 화살표를 로봇에 보냈는지. 보냈으면 버튼은 '완료'로 둔다 (로봇 검사 결과와 별개).
  // 화살표를 보낸 시각. 로봇이 READY 를 보낼 때까지 '위치 추정 중'으로 둔다.
  const [poseSentAt, setPoseSentAt] = useState(null);
  const poseLockRef = useRef(false);
  const autoSendPoseRef = useRef(null);
  const [goalStep, setGoalStep] = useState("idle");
  const [zoneKind] = useState(null);
  const [zoneBoxes, setZoneBoxes] = useState({ region: null, boundary: null });
  const [zoneDragging, setZoneDragging] = useState(false);
  const [wardScents, setWardScents] = useState(loadWardScents);
  const poseDragRef = useRef(null);
  const zoneDragRef = useRef(null);
  const destinationRef = useRef(null);
  const navEpochRef = useRef(0);
  const previousSelectedIdRef = useRef(null);

  const { pose: gdmPose, status: gdmStatus } = useGdmRobotPose(true);

  useEffect(() => {
    const refreshScents = () => setWardScents(loadWardScents());
    window.addEventListener("air-scent-ward-scents-changed", refreshScents);
    window.addEventListener("focus", refreshScents);
    return () => {
      window.removeEventListener("air-scent-ward-scents-changed", refreshScents);
      window.removeEventListener("focus", refreshScents);
    };
  }, []);

  useMoveUiSync({
    wards,
    setWards,
    setSavedWards,
    selectedId,
    setSelectedId,
    dropPin,
    setDropPin,
    navGoal,
    setNavGoal,
    isNavigating,
    setIsNavigating,
    mode,
    setMode,
    poseEstimating,
    setPoseEstimating,
    locStatus,
    setLocStatus,
    missionPhase,
    setMissionPhase,
    manualControl,
    setManualControl,
    missionReady,
    setMissionReady,
    lastEvent,
    setLastEvent,
    localizationState,
    setLocalizationState,
    draggingId,
    poseDragging: poseDragging || goalDragging,
    poseLockRef,
  });

  useEffect(() => {
    if (!poseEstimating && dropPin) setDropPin(null);
  }, [poseEstimating, dropPin]);

  // 보낸 화살표를 로봇이 확정하면 '완료'로
  useEffect(() => {
    if (poseSentAt == null) return undefined;
    const ready =
      locStatus === "localized" ||
      localizationState === "READY" ||
      localizationState === "DEGRADED" ||
      missionReady === true;
    if (!ready) return undefined;
    // 보내기 직전의 옛 READY 로 바로 끝나지 않게 2.5초는 기다린다
    const wait = Math.max(0, 2500 - (Date.now() - poseSentAt));
    const timer = window.setTimeout(() => {
      setPoseSentAt(null);
      setMoveStatus("위치 추정 완료");
    }, wait);
    return () => window.clearTimeout(timer);
  }, [poseSentAt, localizationState, locStatus, missionReady]);

  // 실패 메시지만 5초 동안 지도 위에 띄운다
  useEffect(() => {
    const text = String(moveStatus || "");
    if (!/실패|못했|못 했|오류|없음|꺼져/.test(text)) return undefined;
    setFlashError(text);
    const timer = window.setTimeout(() => setFlashError(null), 5000);
    return () => window.clearTimeout(timer);
  }, [moveStatus]);


  useEffect(() => {
    if (goalStep === "zones") return;
    if (String(lastEvent || "").includes("목표 저장 완료")) {
      setGoalStep("zones");
      setMoveStatus("정찰 제한 구역과 한계 구역을 설정하세요");
    }
  }, [goalStep, lastEvent]);

  const commitWards = useCallback((nextWards) => {
    const snapshot = nextWards.slice(0, MAX_WARDS).map((ward) => ({ ...ward }));
    saveWards(snapshot);
    setSavedWards(snapshot);
  }, []);

  const selectedPinDirty = useMemo(() => {
    if (!selectedId) return false;
    const current = wards.find((ward) => ward.id === selectedId);
    if (!current) return false;
    const saved = savedWards.find((ward) => ward.id === selectedId);
    if (!saved) return true;
    return (
      current.x !== saved.x ||
      current.y !== saved.y ||
      current.name !== saved.name
    );
  }, [wards, savedWards, selectedId]);

  const mapRotation = robotMap.rotation || 0;
  const mapZoom = robotMap.zoom || 100;
  const mapCenterX = Number.isFinite(Number(robotMap.centerX)) ? Number(robotMap.centerX) : 50;
  const mapCenterY = Number.isFinite(Number(robotMap.centerY)) ? Number(robotMap.centerY) : 50;
  const mapRotationRef = useRef(0);
  mapRotationRef.current = mapRotation;

  // 전체화면: 한계구역이 화면에 꽉 차게 (보기만 — 핀·로봇·클릭 좌표는 같은 layout 을 따른다) (11/15)
  const [fitBoundary, setFitBoundary] = useState(null);
  const [roomRect, setRoomRect] = useState(null);
  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      try {
        const zones = await fetchPatrolZones();
        if (cancelled) return;
        if (zones?.boundary) {
          setFitBoundary((prev) => (JSON.stringify(prev) === JSON.stringify(zones.boundary) ? prev : zones.boundary));
        }
        setRoomRect(zones?.rectangle ?? null);
      } catch {
        // 직전 구역 유지
      }
    };
    tick();
    const timer = window.setInterval(tick, 10000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, []);
  const roomScent = useMemo(() => {
    const ward = wards.find((item) => !isKitchenWard(item)) ?? wards[0];
    if (!ward || wardScents?.[ward.id]?.unset) return null;
    const fragrance = getWardScent(wardScents, ward.id).fragrance;
    if (fragrance === "Lavender") return { tone: "lavender", label: "라벤더" };
    if (fragrance === "Orange") return { tone: "orange", label: "오렌지" };
    if (fragrance === "Mint") return { tone: "mint", label: "페퍼민트" };
    return null;
  }, [wards, wardScents]);

  const roomPolygon = useMemo(
    () => orientedRectToMapPolygon(roomRect, mapMeta),
    [roomRect, mapMeta]
  );
  const roomPolygonRef = useRef(null);
  roomPolygonRef.current = roomPolygon;
  const boundaryPolygon = useMemo(
    () => orientedRectToMapPolygon(fitBoundary, mapMeta),
    [fitBoundary, mapMeta]
  );
  const boundaryClip = useMemo(() => {
    if (!mapExpanded || !boundaryPolygon || !mapLayout) return null;
    const points = boundaryPolygon.map(([x, y]) => {
      const screen = mapPercentToContainer(x, y, mapLayout);
      return `${screen.x}% ${screen.y}%`;
    });
    if (points.length < 3) return null;
    return `polygon(${points.join(", ")})`;
  }, [mapExpanded, boundaryPolygon, mapLayout]);
  const roomClip = useMemo(() => {
    if (!roomPolygon || !mapLayout) return null;
    const points = roomPolygon.map(([x, y]) => {
      const screen = mapPercentToContainer(x, y, mapLayout);
      return {
        svg: `${(screen.x / 100) * mapLayout.containerW},${(screen.y / 100) * mapLayout.containerH}`,
        css: `${screen.x}% ${screen.y}%`,
      };
    });
    if (points.length < 3) return null;
    return {
      svg: points.map((point) => point.svg).join(" "),
      css: `polygon(${points.map((point) => point.css).join(", ")})`,
    };
  }, [roomPolygon, mapLayout]);

  const updateLayout = useCallback(() => {
    const container = mapRef.current;
    const img = imgRef.current;
    if (!container || !img?.naturalWidth) return;

    const fit = mapExpanded
      ? boundaryFitView(fitBoundary, mapMeta, img.naturalWidth, img.naturalHeight,
          container.clientWidth, container.clientHeight, mapRotation, 1)
      : null;
    setMapLayout(
      computeContainLayout(
        container.clientWidth,
        container.clientHeight,
        img.naturalWidth,
        img.naturalHeight,
        mapRotation,
        fit ? fit.zoom : mapZoom,
        fit ? fit.centerX : mapCenterX,
        fit ? fit.centerY : mapCenterY,
      )
    );
  }, [mapRotation, mapZoom, mapCenterX, mapCenterY, mapExpanded, fitBoundary, mapMeta]);

  useLayoutEffect(() => {
    if (!mapReady) return undefined;
    updateLayout();
    const container = mapRef.current;
    if (!container || typeof ResizeObserver === "undefined") return undefined;

    const observer = new ResizeObserver(() => updateLayout());
    observer.observe(container);
    return () => observer.disconnect();
  }, [mapReady, updateLayout]);

  const importGdmRooms = useCallback(async () => {
    setGdmImporting(true);
    try {
      if (robotMapRef.current.meta) {
        setMapMeta(robotMapRef.current.meta);
        setRoomPolys([]);
        return;
      }
      const payload = await fetchGdmRooms();
      setMapMeta({
        resolution: payload?.resolution ?? null,
        origin: payload?.origin ?? null,
        image_size: payload?.image_size ?? null,
        map: payload?.map ?? null,
      });
      const nextWards = roomsToWards(payload);
      setRoomPolys(
        nextWards
          .filter((ward) => ward.polygon?.length)
          .map((ward) => ({
            id: ward.id,
            polygon: ward.polygon,
          }))
      );

      if (nextWards.length === 0) {
        return;
      }

      const mapped = nextWards
        .map(({ polygon, targetScent, source, ...ward }) => ({
          ...ward,
          sensorId: ward.sensorId ?? null,
        }))
        .slice(0, MAX_WARDS);

      let alreadySynced = localStorage.getItem(GDM_SYNC_KEY) === "1";
      let hasSharedWards = false;
      if (!alreadySynced) {
        try {
          const ui = await fetchMoveUi();
          if (Array.isArray(ui?.wards) && ui.wards.length > 0) {
            alreadySynced = true;
            hasSharedWards = true;
            localStorage.setItem(GDM_SYNC_KEY, "1");
          }
        } catch {
          // 서버에 아직 공유 상태가 없으면 로컬 최초 세팅으로 진행
        }
      }

      // 최초 1회만 GDM 좌표로 집 위치를 세팅 (다른 PC에 이미 있으면 덮어쓰지 않음)
      if (!alreadySynced) {
        setWards(mapped);
        commitWards(mapped);
        setSelectedId(mapped[0]?.id ?? null);
        localStorage.setItem(GDM_SYNC_KEY, "1");
        return;
      }

      if (hasSharedWards) {
        return;
      }

      // 이후 불러오기: 방 윤곽만 갱신하고, 기존 핀 위치·이름은 유지
      setWards((prev) => {
        const limitedPrev = prev.slice(0, MAX_WARDS);
        const prevById = new Map(limitedPrev.map((ward) => [ward.id, ward]));

        const mergedFromGdm = mapped.map((ward, index) => {
          const existing =
            prevById.get(ward.id) ?? limitedPrev[index] ?? null;
          if (!existing) return ward;
          return {
            ...ward,
            id: existing.id,
            x: existing.x,
            y: existing.y,
            name: existing.name,
          };
        });

        // 저장해 둔 집 위치를 우선 유지 (GDM id가 달라도 위치 보존)
        const merged =
          mergedFromGdm.length >= MAX_WARDS
            ? mergedFromGdm.slice(0, MAX_WARDS)
            : [
                ...mergedFromGdm,
                ...limitedPrev.filter(
                  (ward) => !mergedFromGdm.some((item) => item.id === ward.id)
                ),
              ].slice(0, MAX_WARDS);

        commitWards(merged);
        return merged;
      });
    } catch (error) {
      console.warn("[move] GDM rooms import failed:", error);
    } finally {
      setGdmImporting(false);
    }
  }, [commitWards]);

  useEffect(() => {
    if (robotMap.status === "loading") return;
    if (robotMap.meta) {
      setMapMeta(robotMap.meta);
      setRoomPolys([]);
      return;
    }
    importGdmRooms();
  }, [robotMap.status, robotMap.signature, robotMap.meta, importGdmRooms]);

  useEffect(() => {
    let cancelled = false;

    async function tick() {
      try {
        const ui = await fetchMoveUi();
        if (!cancelled) {
          setSharedSpraying(Boolean(ui?.fragranceSpraying));
        }
      } catch {
        // keep last shared spray flag
      }
    }

    tick();
    const timer = window.setInterval(tick, 1000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, []);

  const selectedWard = useMemo(
    () => wards.find((ward) => ward.id === selectedId) ?? null,
    [wards, selectedId]
  );
  const aiTargetWard = useMemo(
    () => wards.find((ward) => !isKitchenWard(ward)) ?? selectedWard ?? wards[0] ?? null,
    [wards, selectedWard]
  );

  useEffect(() => subscribeDeveloperMode(setDeveloperMode), []);

  useEffect(() => {
    if (mode === "move" || poseEstimating) {
      setScentPanelWardId(null);
      setAiChatOpen(false);
      setAiChatLive(false);
    }
  }, [mode, poseEstimating]);

  useEffect(() => {
    setScentPanelView("buttons");
  }, [scentPanelWardId]);

  const liveRobotMap = useMemo(() => {
    if (!mapLayout) return null;
    return poseToMapPercent(
      gdmPose,
      mapLayout.naturalW,
      mapLayout.naturalH,
      mapMeta
    );
  }, [gdmPose, mapLayout, mapMeta]);

  const robotMapPos = liveRobotMap?.live ? liveRobotMap : null;
  const robotScreen = robotMapPos
    ? mapPercentToContainer(robotMapPos.x, robotMapPos.y, mapLayout)
    : null;

  const fallbackReading =
    mqttAirQuality?.hasData || mqttAirQuality?.label
      ? {
          label: mqttAirQuality.label,
          confidence: mqttAirQuality.confidence,
          isOrange: mqttAirQuality.isOrange,
          tone: mqttAirQuality.tone,
        }
      : null;

  const wardJudgment = useWardJudgment();
  const odorNowRef = useRef(false);
  odorNowRef.current = isOdorDepart(mqttAirQuality, wardJudgment);
  const wardVisuals = useMemo(() => {
    const rooms = mqttAirQuality?.rooms ?? {};
    const map = {};
    const fallback =
      fallbackReading ??
      (mqttAirQuality?.label
        ? {
            label: mqttAirQuality.label,
            confidence: mqttAirQuality.confidence,
            isOrange: mqttAirQuality.isOrange,
            tone: mqttAirQuality.tone,
            raw: mqttAirQuality.label,
          }
        : null);

    for (const ward of wards) {
      let reading = readingForWard(rooms, ward, null);

      if (!reading && !isKitchenWard(ward)) {
        // 방별 토픽이 아직 없으면 전역 Fresh Air 등을 와드에 표시
        reading = rooms.default ?? fallback;
      }

      map[ward.id] = getScentVisual(reading, {
        connected: isKitchenWard(ward) || Boolean(mqttAirQuality?.connected),
      });
    }

    return applyWardJudgment(map, wards, wardJudgment);
  }, [wards, mqttAirQuality, fallbackReading, wardJudgment]);

  const roomColor = useMemo(() => {
    const ward = wards.find((item) => !isKitchenWard(item)) ?? wards[0];
    return paintRoom(ward ? wardVisuals[ward.id] : null);
  }, [wards, wardVisuals]);

  const roomEmoji = useMemo(() => {
    const ward = wards.find((item) => !isKitchenWard(item)) ?? wards[0];
    const visual = ward ? wardVisuals[ward.id] : null;
    if (
      !visual?.emoji ||
      visual.tone === "waiting" ||
      visual.tone === "offline" ||
      visual.tone === "fresh" ||
      !mapLayout
    ) {
      return null;
    }
    let point = null;
    if (roomPolygon?.length) {
      const x = roomPolygon.reduce((sum, pair) => sum + pair[0], 0) / roomPolygon.length;
      const y = roomPolygon.reduce((sum, pair) => sum + pair[1], 0) / roomPolygon.length;
      point = mapPercentToContainer(x, y, mapLayout);
    } else if (ward) {
      point = mapPercentToContainer(ward.x, ward.y, mapLayout);
    }
    if (!point) return null;
    return { x: point.x, y: point.y, emoji: visual.emoji };
  }, [wards, wardVisuals, mapLayout, roomPolygon]);

  const placePoint = useCallback(
    (clientX, clientY) => {
      const el = mapRef.current;
      if (!el || mapAnimatingRef.current) return null;
      const rect = el.getBoundingClientRect();
      return containerPointToMapPercent(clientX, clientY, rect, mapLayout);
    },
    [mapLayout]
  );

  const endWardDrag = useCallback(() => {
    dragRef.current = null;
    setDraggingId(null);
  }, []);

  const moveWardToPointer = useCallback(
    (clientX, clientY) => {
      const drag = dragRef.current;
      if (!drag) return;
      const point = placePoint(clientX, clientY);
      if (!point) return;

      const x = Number(point.x.toFixed(1));
      const y = Number(point.y.toFixed(1));
      setWards((prev) =>
        prev.map((ward) =>
          ward.id === drag.id ? { ...ward, x, y } : ward
        )
      );
    },
    [placePoint]
  );

  useEffect(() => {
    if (!draggingId) return undefined;

    const onMove = (event) => {
      if (dragRef.current?.pointerId !== event.pointerId) return;
      event.preventDefault();
      moveWardToPointer(event.clientX, event.clientY);
    };

    const onUp = (event) => {
      if (dragRef.current?.pointerId !== event.pointerId) return;
      endWardDrag();
    };

    window.addEventListener("pointermove", onMove, { passive: false });
    window.addEventListener("pointerup", onUp);
    window.addEventListener("pointercancel", onUp);

    return () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointercancel", onUp);
    };
  }, [draggingId, moveWardToPointer, endWardDrag]);

  const beginGoalDrag = (event) => {
    if (poseEstimating || zoneKind || isMoving) return;
    event.stopPropagation();
    event.preventDefault();
    goalDragRef.current = { pointerId: event.pointerId };
    setGoalDragging(true);
  };

  useEffect(() => {
    if (!goalDragging) return undefined;

    const moveTo = (event) => {
      const point = placePoint(event.clientX, event.clientY);
      if (!point) return null;
      const kept = clampToRoom(point.x, point.y, roomPolygonRef.current);
      return { x: Number(kept.x.toFixed(1)), y: Number(kept.y.toFixed(1)) };
    };

    const onMove = (event) => {
      if (goalDragRef.current?.pointerId !== event.pointerId) return;
      event.preventDefault();
      const p = moveTo(event);
      if (p) setNavGoal((prev) => ({ yaw: 0, ...(prev || {}), x: p.x, y: p.y }));
    };

    const onUp = (event) => {
      if (goalDragRef.current?.pointerId !== event.pointerId) return;
      goalDragRef.current = null;
      setGoalDragging(false);
      const p = event.type === "pointercancel" ? null : moveTo(event);
      if (!p) return;
      setNavGoal((prev) => ({ yaw: 0, ...(prev || {}), x: p.x, y: p.y }));
      // 향기 효과·출동 표시가 와드를 따라오게 첫 집 위치도 같이 옮긴다
      setWards((prev) => {
        if (!prev.length) return prev;
        const next = prev.map((ward, index) => (index === 0 ? { ...ward, x: p.x, y: p.y } : ward));
        commitWards(next);
        return next;
      });
      setMoveStatus("와드 위치를 옮겼습니다 (화면에만 저장)");
    };

    window.addEventListener("pointermove", onMove, { passive: false });
    window.addEventListener("pointerup", onUp);
    window.addEventListener("pointercancel", onUp);
    return () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointercancel", onUp);
    };
  }, [goalDragging, placePoint, commitWards]);

  const updatePoseDrag = useCallback((clientX, clientY) => {
    const drag = poseDragRef.current;
    if (!drag) return;

    const dx = clientX - drag.startClient.x;
    const dy = clientY - drag.startClient.y;
    const dragPx = Math.hypot(dx, dy);
    const yaw =
      dragPx < 16 ? drag.startYaw : screenDragToMapYaw(dx, dy, mapRotationRef.current);

    setDropPin({
      x: drag.start.x,
      y: drag.start.y,
      yaw,
      dragPx,
    });
  }, []);

  const endPoseDrag = useCallback((event) => {
    const drag = poseDragRef.current;
    poseDragRef.current = null;
    setPoseDragging(false);
    if (!drag || !event || event.type === "pointercancel") return;

    // 손을 떼면 그 화살표를 바로 로봇에 보낸다
    const dx = event.clientX - drag.startClient.x;
    const dy = event.clientY - drag.startClient.y;
    const dragPx = Math.hypot(dx, dy);
    const yaw =
      dragPx < 16 ? drag.startYaw : screenDragToMapYaw(dx, dy, mapRotationRef.current);
    const pin = { x: drag.start.x, y: drag.start.y, yaw, dragPx };
    setDropPin(pin);
    autoSendPoseRef.current?.(pin);
  }, []);

  useEffect(() => {
    if (!poseDragging) return undefined;

    const onMove = (event) => {
      if (poseDragRef.current?.pointerId !== event.pointerId) return;
      event.preventDefault();
      updatePoseDrag(event.clientX, event.clientY);
    };

    const onUp = (event) => {
      if (poseDragRef.current?.pointerId !== event.pointerId) return;
      endPoseDrag(event);
    };

    window.addEventListener("pointermove", onMove, { passive: false });
    window.addEventListener("pointerup", onUp);
    window.addEventListener("pointercancel", onUp);

    return () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointercancel", onUp);
    };
  }, [poseDragging, updatePoseDrag, endPoseDrag]);

  useEffect(() => {
    if (!zoneDragging) return undefined;

    const onMove = (event) => {
      const drag = zoneDragRef.current;
      if (!drag || drag.pointerId !== event.pointerId) return;
      const point = placePoint(event.clientX, event.clientY);
      if (!point) return;
      const box = {
        x1: drag.start.x,
        y1: drag.start.y,
        x2: Number(point.x.toFixed(1)),
        y2: Number(point.y.toFixed(1)),
      };
      setZoneBoxes((prev) => ({ ...prev, [drag.kind]: box }));
    };

    const onUp = (event) => {
      if (zoneDragRef.current?.pointerId !== event.pointerId) return;
      zoneDragRef.current = null;
      setZoneDragging(false);
    };

    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    window.addEventListener("pointercancel", onUp);
    return () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointercancel", onUp);
    };
  }, [placePoint, zoneDragging]);

  const scentConversationOpen =
    scentPanelView === "chat" || scentPanelView === "text";

  const handleMapPointer = (event) => {
    if (mapAnimatingRef.current) return;
    if (scentConversationOpen) return;
    if (scentPanelWardId && !event.target.closest(".slam-ward")) {
      setScentPanelWardId(null);
      return;
    }
    if (isMoving || draggingId || poseDragging || zoneDragging) return;
    if (zoneKind && !poseEstimating) {
      const point = placePoint(event.clientX, event.clientY);
      if (!point) return;
      event.preventDefault();
      const start = {
        x: Number(point.x.toFixed(1)),
        y: Number(point.y.toFixed(1)),
      };
      zoneDragRef.current = { pointerId: event.pointerId, start, kind: zoneKind };
      setZoneDragging(true);
      setZoneBoxes((prev) => ({
        ...prev,
        [zoneKind]: { x1: start.x, y1: start.y, x2: start.x, y2: start.y },
      }));
      try {
        event.currentTarget.setPointerCapture?.(event.pointerId);
      } catch {
        /* ignore */
      }
      return;
    }
    if (!poseEstimating && locStatus === "not_localized") return;
    if (!poseEstimating && event.target.closest(".slam-ward")) return;
    if (event.target.closest(".slam-pin-actions")) return;
    if (event.target.closest(".slam-map-tools")) return;
    if (event.target.closest(".slam-destination")) return;

    if (mode === "move" && !poseEstimating) {
      setSelectedId(null);
      return;
    }

    if (!poseEstimating) return;

    const point = placePoint(event.clientX, event.clientY);
    if (!point) return;
    event.preventDefault();
    setSelectedId(null);

    const start = {
      x: Number(point.x.toFixed(1)),
      y: Number(point.y.toFixed(1)),
    };
    poseDragRef.current = {
      pointerId: event.pointerId,
      start,
      startClient: { x: event.clientX, y: event.clientY },
      startYaw: 0,
    };
    setPoseDragging(true);
    setDropPin({
      x: start.x,
      y: start.y,
      yaw: 0,
      dragPx: 0,
    });
    setMoveStatus("드래그해서 방향을 정하세요");
    try {
      event.currentTarget.setPointerCapture?.(event.pointerId);
    } catch {
      /* ignore */
    }
  };


  const saveSelectedPin = () => {
    if (!selectedId || isMoving) return;
    const ward = wards.find((item) => item.id === selectedId);
    commitWards(wards);
    if (ward) {
      const yaw = yawFromRobotToMapPercent(gdmPose, ward.x, ward.y, mapMeta, {
        reverse: true,
      });
      const nextGoal = {
        x: Number(ward.x),
        y: Number(ward.y),
        yaw: Number.isFinite(yaw) ? yaw : 0,
      };
      setNavGoal(nextGoal);
      setDropPin(null);
      void putMoveUi({
        wards,
        selectedId,
        dropPin: null,
        navGoal: nextGoal,
        mode: "select",
      }).catch(() => {});
    }
    setMode("select");
    setMoveStatus("핀 위치 저장됨");
  };

  const cancelSelectedPin = () => {
    if (!selectedId || isMoving) return;

    const saved = savedWards.find((ward) => ward.id === selectedId);
    if (!saved) {
      setMode("select");
      return;
    }

    setWards((prev) =>
      prev.map((ward) =>
        ward.id === selectedId
          ? { ...ward, x: saved.x, y: saved.y, name: saved.name }
          : ward
      )
    );
    setMode("select");
    setMoveStatus("위치 변경 취소");
  };

  const resolveTargetPercent = useCallback(() => {
    if (poseEstimating) return null;
    if (dropPin) {
      return {
        x: dropPin.x,
        y: dropPin.y,
        label: "핀",
        reverse: false,
      };
    }
    if (selectedWard) {
      return {
        x: selectedWard.x,
        y: selectedWard.y,
        label: wardDisplayName(selectedWard),
        reverse: true,
      };
    }
    return null;
  }, [dropPin, poseEstimating, selectedWard]);

  const publishAtMapPercent = useCallback(
    async (kind, xPercent, yPercent, label = "지점", yaw = 0) => {
      if (isMoving || isStopping) return;

      const world = mapPercentToWorldMeters(xPercent, yPercent, mapMeta);
      if (!world) {
        setMoveStatus("지도 메타 없음 — GDM 방 정보를 불러오세요");
        return;
      }

      const isGoal = kind === "goal";
      const epoch = navEpochRef.current;
      if (isGoal) {
        setNavGoal({ x: Number(xPercent), y: Number(yPercent), yaw });
        setIsNavigating(true);
      }
      setIsMoving(true);
      setMoveStatus(isGoal ? "목표 전송 중" : "위치 추정 전송 중");

      try {
        const payload = {
          x: world.x,
          y: world.y,
          yaw,
          frame_id: "map",
        };
        if (isGoal) {
          await sendNavGoal(payload);
        } else {
          await sendInitialPose(payload);
        }
        if (epoch !== navEpochRef.current) {
          if (isGoal) {
            try {
              await cancelNavGoal();
            } catch {
              // already cancelled from the stop button
            }
          }
          setIsMoving(false);
          return;
        }
        setIsMoving(false);
        if (isGoal) {
          setMoveStatus(
            `이동 중 (${world.x.toFixed(1)}, ${world.y.toFixed(1)}, ${yawToDisplayDeg(yaw)}°)`
          );
          if (odorNowRef.current) {
            announceOdorDepart();
          } else if (label && label !== "핀") {
            announceScentMission(label);
          } else {
            announceMoveTo(label === "핀" ? "" : label);
          }
          return;
        }
        poseLockRef.current = false;
        setPoseSentAt(Date.now());
        setMoveStatus("위치 추정 중");
        setPoseEstimating(false);
        setDropPin(null);
        if (previousSelectedIdRef.current) {
          setSelectedId(previousSelectedIdRef.current);
        }
      } catch (error) {
        setIsMoving(false);
        if (isGoal && epoch === navEpochRef.current) {
          setIsNavigating(false);
        }
        setMoveStatus(
          error?.message || (isGoal ? "목표 전송 실패" : "위치 추정 실패")
        );
      }
    },
    [isMoving, isStopping, mapMeta]
  );
  useEffect(() => {
    autoSendPoseRef.current = (pin) =>
      publishAtMapPercent("initial", pin.x, pin.y, "핀", Number(pin.yaw) || 0);
  }, [publishAtMapPercent]);

  const startMove = (options = {}) => {
    if (isMoving || isStopping || poseEstimating || manualControl) return;
    if (locStatus === "not_localized" || missionReady === false) {
      if (options.sprayFirst) {
        setMoveStatus("위치 추정 후 와드로 가서 발향할 수 있습니다");
      }
      return;
    }

    void (async () => {
      const isScent = Boolean(selectedWard && !dropPin && !poseEstimating);
      const target = resolveTargetPercent();
      let nextGoal = null;
      if (target) {
        const yaw = target.reverse
          ? yawFromRobotToMapPercent(gdmPose, target.x, target.y, mapMeta, {
              reverse: true,
            })
          : Number(dropPin?.yaw) || 0;
        nextGoal = {
          x: Number(target.x),
          y: Number(target.y),
          yaw: Number.isFinite(yaw) ? yaw : 0,
        };
        setNavGoal(nextGoal);
      }
      setIsMoving(true);
      setIsNavigating(true);
      setMoveStatus(options.sprayFirst ? "와드로 이동" : "목표로 복귀");
      if (isScent) {
        await captureScentReturnPose(gdmPose, mapMeta);
      } else {
        clearScentReturn();
      }
      try {
        if (options.sprayFirst) {
          await putMoveUi({
            sprayFirst: true,
            sprayChannel: options.channel || null,
          });
        }
        await sendNavGoal({ x: 0, y: 0, yaw: 0, frame_id: "map" });
        const odorNow = odorNowRef.current && !options.sprayFirst;
        await putMoveUi({
          selectedId: isScent ? selectedWard.id : selectedId,
          dropPin: null,
          ...(nextGoal ? { navGoal: nextGoal } : {}),
          isNavigating: true,
          scentMission: true,
          mode: "select",
          ...(odorNow ? { sprayFirst: false, sprayChannel: null } : {}),
        });
        setIsMoving(false);
        setMoveStatus(options.sprayFirst ? "와드로 이동 중" : "목표로 복귀 중");
        if (options.sprayFirst) announceScentConfigured(options.channel);
        else if (odorNow) announceOdorDepart();
        else if (isScent) announceScentMission(selectedWard);
        else announceMoveTo("목표");
      } catch (error) {
        setIsMoving(false);
        setIsNavigating(false);
        if (options.sprayFirst) {
          void putMoveUi({ sprayFirst: false, sprayChannel: null }).catch(() => {});
        }
        setMoveStatus(error?.message || (options.sprayFirst ? "와드 출발 실패" : "목표 복귀 실패"));
      }
    })();
  };

  const stopMove = useCallback(async () => {
    if (isStopping) return;
    navEpochRef.current += 1;
    setIsStopping(true);
    setMoveStatus("정찰 재개");
    try {
      await cancelNavGoal();
      clearScentReturn();
      setIsNavigating(false);
      setIsMoving(false);
      try {
        await putMoveUi({ isNavigating: false, scentMission: false });
      } catch {
        // go_around already applied
      }
      setMoveStatus("정찰 중");
      window.setTimeout(() => {
        setMoveStatus((current) => (current === "정찰 중" ? "대기" : current));
      }, 1800);
    } catch (error) {
      setMoveStatus(error?.message || "정찰 재개 실패");
    } finally {
      setIsStopping(false);
    }
  }, [isStopping]);

  const beginPoseEstimate = useCallback(() => {
    // 로봇이 위치를 찾는 중이어도 언제든 다시 지정할 수 있다
    if (isMoving || poseDragging) return;
    previousSelectedIdRef.current = selectedId;
    poseLockRef.current = true;
    setPoseSentAt(null);
    setPoseEstimating(true);
    setSelectedId(null);
    setDropPin(null);
    setMode("select");
    setMoveStatus("지도를 누른 채 드래그해 방향 화살표를 만드세요");
  }, [isMoving, poseDragging, selectedId]);

  const cancelPoseEstimate = useCallback(() => {
    if (isMoving) return;
    poseLockRef.current = false;
    poseDragRef.current = null;
    setPoseDragging(false);
    setPoseEstimating(false);
    setDropPin(null);
    if (previousSelectedIdRef.current) {
      setSelectedId(previousSelectedIdRef.current);
    }
    setMoveStatus("대기");
  }, [isMoving]);

  const confirmPoseEstimate = useCallback(() => {
    if (isMoving || isStopping || poseDragging || !dropPin) return false;

    void publishAtMapPercent(
      "initial",
      dropPin.x,
      dropPin.y,
      "핀",
      Number(dropPin.yaw) || 0,
    );
    return true;
  }, [dropPin, isMoving, isStopping, poseDragging, publishAtMapPercent]);

  // 지도에 그려 정하는 구역 설정(위치 제한 구역·한계 구역)은 없앴다. 구역은 ROS 에서 정한다.

  // 로봇 기준 '위치 확정': READY·DEGRADED(로봇이 ready=true 로 주행) 또는 임무 가능
  const robotLocalized =
    locStatus === "localized" ||
    localizationState === "READY" ||
    localizationState === "DEGRADED" ||
    missionReady === true;
  // 보낸 뒤 로봇이 확정(READY)할 때까지는 '추정 중'. 중간의 SEARCHING↔FAILED 로 깜빡이지 않는다.
  const posePendingLoc = !poseEstimating && poseSentAt != null;
  const poseDone = !poseEstimating && !posePendingLoc && robotLocalized;
  const poseLabel = poseEstimating
    ? "위치 지정 중"
    : posePendingLoc
      ? "위치 추정 중"
      : poseDone
        ? "위치 추정 완료"
        : "위치 추정 하기";
  const notLocalized = locStatus === "not_localized" && !robotLocalized;
  const notReady = missionReady === false;
  const goBlocked = notLocalized || notReady || manualControl;
  const modeHint = zoneKind
    ? zoneKind === "region"
      ? "지도를 드래그해서 위치 제한 구역을 그린 뒤 저장하세요"
      : "지도를 드래그해서 한계 구역을 그린 뒤 저장하세요. 한계는 목표를 포함해야 합니다"
    : poseEstimating
    ? dropPin
      ? "손을 떼면 바로 로봇에 보냅니다. 다시 그리면 다시 보냅니다"
      : "지도를 누른 채 드래그하면 방향 화살표가 생깁니다"
    : manualControl
      ? "조이스틱 수동 조작 중입니다"
    : notLocalized
      ? "위치 추정이 필요합니다. 위치 추정만 할 수 있습니다"
      : notReady
        ? "센서 준비가 끝나지 않았습니다"
      : missionPhase === "setup"
        ? "설정 모드입니다. 목표, 파란 한계, 주황 정찰 구간을 저장할 수 있습니다"
      : {
          select: "집을 선택하세요",
          move: "집을 드래그해 위치 수정",
        }[mode];

  // 그리는 중에만 사용법 한 줄
  const drawingHint = poseEstimating || zoneKind ? modeHint : null;


  const selectedScreen = selectedWard
    ? mapPercentToContainer(selectedWard.x, selectedWard.y, mapLayout)
    : null;
  const dropPinScreen = dropPin
    ? mapPercentToContainer(dropPin.x, dropPin.y, mapLayout)
    : null;
  const zoneScreens = ["region", "boundary"]
    .map((kind) => {
      const box = zoneBoxes[kind];
      if (!box || !mapLayout) return null;
      const a = mapPercentToContainer(box.x1, box.y1, mapLayout);
      const b = mapPercentToContainer(box.x2, box.y2, mapLayout);
      if (!a || !b) return null;
      return {
        kind,
        left: Math.min(a.x, b.x),
        top: Math.min(a.y, b.y),
        width: Math.abs(b.x - a.x),
        height: Math.abs(b.y - a.y),
      };
    })
    .filter(Boolean);
  const navGoalScreen = navGoal
    ? mapPercentToContainer(navGoal.x, navGoal.y, mapLayout)
    : null;
  // 와드 표시: 저장된 목표 위치, 없으면 첫 집 위치
  const wardMarker = navGoal || (wards[0] ? { x: wards[0].x, y: wards[0].y, yaw: 0 } : null);
  const wardMarkerScreen = wardMarker
    ? mapPercentToContainer(wardMarker.x, wardMarker.y, mapLayout)
    : null;
  const smellWard = wards.find((item) => !isKitchenWard(item)) ?? wards[0];
  const wardChip = chipWithPercent(
    wardLiveChip(wardJudgment),
    smellWard ? wardVisuals[smellWard.id] : null,
  );
  const pathGoalScreen = isNavigating
    ? navGoalScreen || dropPinScreen || selectedScreen
    : null;
  const hasDestination = Boolean(poseEstimating || dropPin || selectedWard);
  const scentMission = Boolean(selectedWard && !dropPin && !poseEstimating);
  const showPatrolResume = Boolean(isNavigating);
  const showGoButton = showPatrolResume || scentMission;
  const goBusyLabel = isMoving ? "전송 중…" : null;
  const goLabel = goBusyLabel ?? "발향청정";
  const scentSpraying = fragranceOn || fragranceDiffusing || sharedSpraying;

  const handleStopFragrance = () => {
    onStopFragrance?.();
    void stopSharedFragranceSpray();
  };
  const poseArrow =
    dropPin && dropPinScreen && mapLayout
      ? {
          cx: (dropPinScreen.x / 100) * mapLayout.containerW,
          cy: (dropPinScreen.y / 100) * mapLayout.containerH,
          // 길이는 약 1cm 로 고정. 드래그는 방향만 정한다.
          length: POSE_ARROW_PX,
          deg: displayYawCss(dropPin.yaw ?? 0, mapLayout?.rotation),
        }
      : null;

  return (
    <section className="move-page">
      <article
        ref={stageRef}
        className={`slam-stage${stageFixed ? " is-expanded" : ""}`}
      >
        <div
          ref={mapRef}
          className={`slam-map ${
            poseEstimating
              ? "posing estimating-pose"
              : mode === "move"
                ? "relocating"
                : "posing"
          }${poseDragging ? " dragging-pose" : ""}`}
          style={{
            "--map-rotation": `${mapLayout?.rotation ?? mapRotation}deg`,
            "--map-view-scale": mapLayout?.viewScale ?? mapZoom / 100,
            "--map-pan-x": `${mapLayout?.panX ?? 0}px`,
            "--map-pan-y": `${mapLayout?.panY ?? 0}px`,
            "--boundary-clip": boundaryClip ?? "none",
          }}
          onPointerDown={handleMapPointer}
          role="application"
          aria-label="GDM SLAM 지도"
        >
          <div className="slam-map-surface" aria-hidden="true">
            <div className="slam-map-base" />
            <div className="slam-map-grid" />
            {!mapFailed && robotMap.status !== "loading" && (
              <img
                ref={imgRef}
                className={`slam-map-image ${mapReady ? "ready" : ""}`}
                src={
                  robotMap.imageUrl ??
                  `${GDM_FLOORPLAN_URL}?t=${gdmStatus.map ?? "map"}`
                }
                alt=""
                draggable={false}
                onLoad={() => {
                  setMapReady(true);
                  setMapFailed(false);
                  requestAnimationFrame(updateLayout);
                }}
                onError={() => {
                  setMapFailed(true);
                  setMapReady(false);
                }}
              />
            )}
            {/* 보기 전용: 한계구역 안 벽은 매끈한 선, 밖은 흐리게 (원본 지도·좌표는 그대로) */}
            <CleanMapLayer src={robotMap.imageUrl} mapMeta={mapMeta} ready={mapReady} />
            <div className="slam-map-vignette" />
            <div
              className={`slam-map-fallback ${mapFailed || !mapReady ? "visible" : ""}`}
            >
              <p className="slam-fallback-label">
                {mapFailed
                  ? robotMap.imageUrl
                    ? "주행 맵을 불러오지 못했습니다"
                    : "GDM floor_plan PNG를 찾을 수 없습니다 (gdm-bridge 확인)"
                  : "지도 불러오는 중…"}
              </p>
            </div>
          </div>

          {/* 방 윤곽 + 와드 핀 주변만 냄새 칠 (방 폴리곤 전체 채우기 금지) */}
          {mapLayout && (
            <svg className="slam-rooms-svg" aria-hidden="true">
              <defs>
                {roomPolys.map((room) => {
                  const poly = room.polygon ?? [];
                  if (!Array.isArray(poly[0])) return null;
                  const points = poly
                    .map(([x, y]) => {
                      const screen = mapPercentToContainer(x, y, mapLayout);
                      return `${(screen.x / 100) * mapLayout.containerW},${(screen.y / 100) * mapLayout.containerH}`;
                    })
                    .join(" ");
                  if (!points) return null;
                  return (
                    <clipPath
                      key={`clip-${room.id}`}
                      id={`ward-clip-${room.id}`}
                    >
                      <polygon points={points} />
                    </clipPath>
                  );
                })}
              </defs>

              {roomPolys.map((room) => {
                const poly = room.polygon ?? [];
                const wardId = room.id;
                const visual = wardId ? wardVisuals[wardId] : null;
                const activeScent =
                  visual &&
                  visual.tone !== "waiting" &&
                  visual.tone !== "offline";
                if (!Array.isArray(poly[0])) return null;

                const points = poly
                  .map(([x, y]) => {
                    const screen = mapPercentToContainer(x, y, mapLayout);
                    return `${(screen.x / 100) * mapLayout.containerW},${(screen.y / 100) * mapLayout.containerH}`;
                  })
                  .join(" ");
                if (!points) return null;

                return (
                  <polygon
                    key={`outline-${wardId ?? points}`}
                    points={points}
                    className={`slam-room-poly ${activeScent ? `scent-${visual.tone}` : ""}`}
                    style={
                      activeScent
                        ? { stroke: visual.color }
                        : undefined
                    }
                  />
                );
              })}

            </svg>
          )}

          <MapZoneOverlay
            mapLayout={mapLayout}
            mapMeta={mapMeta}
            setupMode={Boolean(zoneKind) || missionPhase === "setup"}
            roomColor={roomColor}
          />

          {mapLayout && (
            <svg className="slam-scent-svg" aria-hidden="true">
              <defs>
                {roomClip ? (
                  <clipPath id="room-scent-clip">
                    <polygon points={roomClip.svg} />
                  </clipPath>
                ) : null}
                {wards.map((ward) => {
                  const visual = wardVisuals[ward.id];
                  const fill = waveFill(visual);
                  if (!fill) return null;
                  const t = colorStrength(visualAmount(visual));
                  const stops = [
                    ["0%", 0.05 + 0.93 * t],
                    ["22%", 0.03 + 0.68 * t],
                    ["48%", 0.015 + 0.4 * t],
                    ["74%", 0.008 + 0.18 * t],
                    ["100%", 0],
                  ];
                  return (
                    <radialGradient
                      key={`grad-${ward.id}`}
                      id={`scent-grad-${ward.id}`}
                      cx="50%"
                      cy="50%"
                      r="50%"
                    >
                      {stops.map(([offset, opacity]) => (
                        <stop
                          key={offset}
                          offset={offset}
                          stopColor={fill}
                          stopOpacity={opacity}
                        />
                      ))}
                    </radialGradient>
                  );
                })}
              </defs>
              {wards.map((ward) => {
                const visual = wardVisuals[ward.id];
                const fill = waveFill(visual);
                if (!fill) return null;
                const screen = mapPercentToContainer(ward.x, ward.y, mapLayout);
                const cx = (screen.x / 100) * mapLayout.containerW;
                const cy = (screen.y / 100) * mapLayout.containerH;
                const radius = scentSpreadRadius(cx, cy, roomClip, mapLayout);
                const hasClip = roomPolys.some((room) => room.id === ward.id);
                const t = colorStrength(visualAmount(visual));
                const strength = 0.1 + 0.9 * t;
                return (
                  <g
                    key={`scent-${ward.id}`}
                    clipPath={
                      roomClip
                        ? "url(#room-scent-clip)"
                        : hasClip
                          ? `url(#ward-clip-${ward.id})`
                          : undefined
                    }
                    style={{ "--wave-strength": strength }}
                  >
                    {SCENT_WAVE_DELAYS.map((delay) => (
                      <circle
                        key={delay}
                        className="slam-scent-wave"
                        cx={cx}
                        cy={cy}
                        r={radius}
                        fill={`url(#scent-grad-${ward.id})`}
                        style={{ animationDelay: `${delay}s` }}
                      />
                    ))}
                    <circle
                      className="slam-local-scent-core"
                      cx={cx}
                      cy={cy}
                      r={Math.min(radius * 0.36, 190)}
                      fill={hexAlpha(fill, 0.04 + 0.78 * t)}
                    />
                  </g>
                );
              })}
            </svg>
          )}

          {isNavigating && pathGoalScreen && robotScreen ? (
            <svg className="slam-path-svg" aria-hidden="true">
              <line
                className="slam-path-line"
                x1={`${robotScreen.x}%`}
                y1={`${robotScreen.y}%`}
                x2={`${pathGoalScreen.x}%`}
                y2={`${pathGoalScreen.y}%`}
              />
            </svg>
          ) : null}

          {zoneScreens.map((zone) => (
            <div
              key={zone.kind}
              className={`slam-zone-draft ${zone.kind}`}
              style={{
                left: `${zone.left}%`,
                top: `${zone.top}%`,
                width: `${zone.width}%`,
                height: `${zone.height}%`,
              }}
            />
          ))}

          {poseArrow ? (
            <svg className="slam-pose-svg" aria-hidden="true">
              <defs>
                <marker
                  id="slam-pose-head"
                  markerWidth="4"
                  markerHeight="4"
                  refX="1"
                  refY="2"
                  orient="auto"
                >
                  <path className="slam-pose-head" d="M0,0 L4,2 L0,4 Z" />
                </marker>
              </defs>
              <g
                className={`slam-pose-arrow${poseDragging ? " dragging" : ""}`}
                transform={`translate(${poseArrow.cx} ${poseArrow.cy}) rotate(${poseArrow.deg})`}
              >
                <circle className="slam-pose-origin" r="5" />
                <line
                  className="slam-pose-shaft"
                  x1="6"
                  y1="0"
                  x2={poseArrow.length - 9}
                  y2="0"
                  markerEnd="url(#slam-pose-head)"
                />
              </g>
            </svg>
          ) : null}

          {wardMarkerScreen && !dropPinScreen ? (
            <div
              className={`slam-nav-goal${goalDragging ? " dragging" : ""}${roomScent ? ` tone-${roomScent.tone}` : ""}`}
              style={{ left: `${wardMarkerScreen.x}%`, top: `${wardMarkerScreen.y}%` }}
              title={
                wardChip?.volts != null
                  ? `와드 · 배터리 ${wardChip.volts.toFixed(2)}V (끌어서 옮기기)`
                  : "와드 (룸 안에서 끌어서 옮기기 · 화면에만 저장)"
              }
              onPointerDown={beginGoalDrag}
            >
              <Crosshair size={16} strokeWidth={2.6} />
              {wardChip ? (
                <span className={`slam-ward-live${wardChip.tone ? ` tone-${wardChip.tone}` : ""}`}>
                  {wardChip.batt ? (
                    <i
                      className={`slam-ward-batt is-${wardChip.batt}`}
                      aria-label={wardChip.batt === "low" ? "배터리 부족" : "배터리 양호"}
                    />
                  ) : null}
                  {wardChip.name ? (
                    <span>
                      {wardChip.name}
                      {wardChip.pct != null ? ` ${wardChip.pct}%` : ""}
                    </span>
                  ) : null}
                </span>
              ) : null}
              <span className="slam-nav-goal-label">와드</span>
              {roomScent ? (
                <span className={`slam-nav-goal-scent tone-${roomScent.tone}`}>
                  {roomScent.label}
                </span>
              ) : null}
            </div>
          ) : null}
          {robotScreen ? (
            <div
              className={`slam-robot ${isMoving || isNavigating ? "moving" : ""} live`}
              style={{
                left: `${robotScreen.x}%`,
                top: `${robotScreen.y}%`,
              }}
              title={
                robotMapPos?.source === "amcl" ||
                robotMapPos?.source === "initialpose"
                  ? "AMCL 로봇 위치"
                  : "GDM 실시간 로봇 위치"
              }
            >
              <span
                className="slam-robot-heading"
                style={{
                  transform: `rotate(${displayYawCss(robotMapPos?.yaw ?? 0, mapLayout?.rotation)}deg)`,
                }}
                aria-hidden="true"
              >
                <span className="slam-robot-nose" />
              </span>
              <span className="slam-robot-inner">
                <Bot size={18} strokeWidth={2.4} />
              </span>
            </div>
          ) : null}

          {/* 집(안방) 핀은 없앴다. 와드 자리에 발향 효과만 남긴다. 퍼짐은 룸 안에서만. */}
          <div
            className="slam-scent-room-clip"
            style={roomClip ? { clipPath: roomClip.css } : undefined}
          >
            {roomEmoji ? (
              <span
                className="slam-room-emoji"
                style={{ left: `${roomEmoji.x}%`, top: `${roomEmoji.y}%` }}
                aria-hidden="true"
              >
                {roomEmoji.emoji}
              </span>
            ) : null}
          </div>

          {selectedPinDirty ? (
            <div
              className="slam-pin-actions"
              onPointerDown={(event) => event.stopPropagation()}
            >
              <button
                type="button"
                className="slam-pin-cancel-btn"
                onClick={cancelSelectedPin}
                disabled={isMoving}
              >
                <X size={18} />
                취소
              </button>
              <button
                type="button"
                className="slam-pin-save-btn"
                onClick={saveSelectedPin}
                disabled={isMoving}
              >
                <Check size={18} />
                위치 저장
              </button>
            </div>
          ) : null}
        <div
            ref={destinationRef}
            className={`slam-destination${destinationCollapsed && !poseEstimating ? " is-collapsed" : ""} ${hasDestination ? "" : "empty"}`}
            style={
              destinationCollapsed && !poseEstimating && destinationHeight
                ? { height: destinationHeight, bottom: destinationDockBottom }
                : undefined
            }
            onPointerDown={(event) => event.stopPropagation()}
            onPointerDownCapture={(event) => event.stopPropagation()}
            onClick={
              destinationCollapsed && !poseEstimating
                ? expandDestination
                : undefined
            }
          >
          {destinationCollapsed && !poseEstimating ? (
            <button
              type="button"
              className="slam-icon-btn slam-destination-toggle"
              onClick={(event) => {
                event.stopPropagation();
                expandDestination();
              }}
              aria-label="목적지 열기"
            >
              <ChevronLeft size={18} />
            </button>
          ) : null}
          {poseEstimating ? (
            <>
              <div className="slam-selected-top">
                <p className="section-label">위치 추정</p>
                <button
                  type="button"
                  className="slam-icon-btn"
                  onClick={cancelPoseEstimate}
                  disabled={isMoving}
                  aria-label="위치 추정 취소"
                >
                  <X size={16} />
                </button>
              </div>

              <h2>{dropPin ? "지정한 위치" : "위치 지정"}</h2>

              {dropPin ? (
                <p className="slam-coord">
                  지도 좌표{" "}
                  <strong>
                    ({dropPin.x.toFixed(1)}, {dropPin.y.toFixed(1)})
                  </strong>
                  <em className="slam-coord-yaw">
                    {` · 방향 ${yawToDisplayDeg(dropPin.yaw)}°`}
                  </em>
                </p>
              ) : (
                <p className="slam-coord">
                  지도를 누른 채 드래그해서 위치와 방향을 정하세요
                </p>
              )}

              <div className={`slam-selected-actions dual-pose${scentSpraying ? " with-stop" : ""}`}>
                <button
                  type="button"
                  className="slam-pin-cancel-btn"
                  onClick={cancelPoseEstimate}
                  disabled={isMoving}
                >
                  <X size={18} />
                  취소
                </button>
                <button
                  type="button"
                  className={`slam-pin-save-btn ${isMoving ? "busy" : ""}`}
                  onClick={confirmPoseEstimate}
                  disabled={isMoving || isStopping || poseDragging || !dropPin}
                >
                  <Check size={18} />
                  {isMoving ? "전송 중…" : "다시 보내기"}
                </button>
                {scentSpraying ? (
                  <button
                    type="button"
                    className={`slam-stop-btn${fragranceSending ? " busy" : ""}`}
                    onClick={handleStopFragrance}
                    disabled={fragranceSending}
                    aria-label="발향 중지"
                  >
                    <Square size={18} strokeWidth={2.4} fill="currentColor" />
                    {fragranceSending ? "중지 중…" : "발향 중지"}
                  </button>
                ) : null}
              </div>
            </>
          ) : (
            <>
              <div className="slam-selected-top">
                <p className="section-label">
                  {dropPin ? "목적지" : selectedWard ? "목적지" : "이동"}
                </p>
                <button
                  type="button"
                  className="slam-icon-btn"
                  onClick={collapseDestination}
                  aria-label="목적지 숨기기"
                >
                  <ChevronRight size={16} />
                </button>
              </div>

              <h2>
                {dropPin
                  ? "클릭한 위치"
                  : selectedWard
                    ? wardDisplayName(selectedWard)
                    : "핀 없음"}
              </h2>

              <p className="slam-coord">
                {dropPin ? (
                  <>
                    지도 좌표{" "}
                    <strong>
                      ({dropPin.x.toFixed(1)}, {dropPin.y.toFixed(1)})
                    </strong>
                    <em className="slam-coord-yaw">
                      {` · 방향 ${yawToDisplayDeg(dropPin.yaw)}°`}
                    </em>
                  </>
                ) : selectedWard ? (
                  <>
                    지도 좌표{" "}
                    <strong>
                      ({selectedWard.x.toFixed(1)}, {selectedWard.y.toFixed(1)})
                    </strong>
                    {selectedPinDirty ? (
                      <em className="slam-coord-dirty"> · 저장 필요</em>
                    ) : (
                      <em className="slam-coord-yaw"> · 도착 후 반대 방향</em>
                    )}
                  </>
                ) : (
                  "집을 선택하세요"
                )}
              </p>

              <div className={`slam-selected-actions dual-pose with-goal${scentSpraying ? " with-stop" : ""}`}>
                {showGoButton ? (
                <button
                  type="button"
                  className={`slam-go-btn ${showPatrolResume ? "is-stop" : ""}${isMoving && !showPatrolResume ? " busy" : ""}${!showPatrolResume && scentMission ? " scent-mission" : ""}${isStopping ? " busy" : ""}`}
                  onClick={showPatrolResume || isMoving ? stopMove : startMove}
                  disabled={
                    isStopping ||
                    poseDragging ||
                    (!(showPatrolResume || isMoving) && goBlocked)
                  }
                >
                  {showPatrolResume ? (
                    <>
                      <Square size={18} strokeWidth={2.4} fill="currentColor" />
                      {isStopping ? "재개 중…" : "정찰 재개"}
                    </>
                  ) : (
                    <>
                      <Wind size={18} />
                      {isMoving ? "전송 중…" : goLabel}
                    </>
                  )}
                </button>
                ) : null}
                {scentSpraying ? (
                  <button
                    type="button"
                    className={`slam-stop-btn${fragranceSending ? " busy" : ""}`}
                    onClick={handleStopFragrance}
                    disabled={fragranceSending}
                    aria-label="발향 중지"
                  >
                    <Square size={18} strokeWidth={2.4} fill="currentColor" />
                    {fragranceSending ? "중지 중…" : "발향 중지"}
                  </button>
                ) : null}
              </div>
              <div className="slam-zone-mini">
                <LocMatchChip />
              </div>
            </>
          )}
        </div>

          <div
            className={`ai-scent-drawer${aiChatOpen ? " is-open" : ""}`}
            onPointerDown={(event) => event.stopPropagation()}
          >
            <div
              className="ai-scent-drawer-panel"
              role="dialog"
              aria-label="AI 향 추천"
              aria-hidden={!aiChatOpen}
              onTransitionEnd={(event) => {
                if (event.target !== event.currentTarget) return;
                if (event.propertyName !== "transform") return;
                if (!aiChatOpen) setAiChatLive(false);
              }}
            >
              <header className="ai-scent-drawer-head">
                <p>AI 향 추천</p>
                <button type="button" onClick={() => setAiChatOpen(false)} aria-label="채팅 닫기">
                  <X size={16} />
                </button>
              </header>
              <div className="ai-scent-drawer-body">
                {aiChatLive && aiTargetWard && scentPanelView === "chat" ? (
                  <AiRecommendPage
                    key={devSeed || "voice"}
                    recommendedFragrance={recommendedFragrance}
                    recommendedReason={recommendedReason}
                    data={data}
                    mqttAirQuality={mqttAirQuality}
                    fragranceCartridgeRemaining={fragranceCartridgeRemaining}
                    lastDispenseAt={lastDispenseAt}
                    targetWardName={wardDisplayName(aiTargetWard)}
                    targetWardId={aiTargetWard.id}
                    initialView="chat"
                    seedPrompt={devSeed}
                    onBack={() => setAiChatOpen(false)}
                    onApplyRecommendation={(payload) => {
                      onApplyFragrance?.(payload);
                      if (!payload?.goSpray) return;
                      const blend = payload.blend || {};
                      const channel = ["mint", "lavender", "orange"].find(
                        (name) => Number(blend[name]) > 0,
                      );
                      startMove({ sprayFirst: true, channel });
                    }}
                  />
                ) : null}
                {aiChatLive && scentPanelView === "text" ? (
                  <form
                    className="map-dev-prompt"
                    onSubmit={(event) => {
                      event.preventDefault();
                      const text = devPrompt.trim();
                      if (!text) return;
                      setDevSeed(text);
                      setScentPanelView("chat");
                    }}
                  >
                    <label htmlFor="map-dev-prompt-input">지금 기분</label>
                    <textarea
                      id="map-dev-prompt-input"
                      value={devPrompt}
                      rows={3}
                      maxLength={120}
                      placeholder="예: 꽃밭에서 쉬고 싶어요"
                      onChange={(event) => setDevPrompt(event.target.value)}
                      autoFocus
                    />
                    <button type="submit" disabled={!devPrompt.trim()}>
                      추천받기
                    </button>
                  </form>
                ) : null}
              </div>
            </div>
            <button
              type="button"
              className="ai-scent-peek"
              aria-expanded={aiChatOpen}
              onClick={() => {
                if (aiChatOpen) {
                  setAiChatOpen(false);
                  return;
                }
                setDevSeed("");
                setScentPanelView(developerMode ? "text" : "chat");
                setAiChatLive(true);
                setAiChatOpen(true);
              }}
            >
              <Sparkles size={15} />
              <span>AI 향 추천</span>
            </button>
          </div>

          <div
            className="slam-map-tools"
            onPointerDown={(event) => event.stopPropagation()}
          >
            <div className="slam-map-tools-row">
              <button
                type="button"
                className={`slam-pose-corner${poseDone ? " is-done" : " is-needed"}`}
                onClick={beginPoseEstimate}
                disabled={isMoving || poseDragging}
                aria-label={poseLabel}
              >
                <LocateFixed size={16} />
                <span>
                  {poseEstimating
                    ? "지정 중"
                    : posePendingLoc
                      ? "추정 중"
                      : poseDone
                        ? "추정 완료"
                        : "위치 추정"}
                </span>
              </button>
              <RobotLaunchChip className="slam-launch-corner" />
              <button
                type="button"
                className="slam-fullscreen-btn"
                aria-pressed={mapExpanded}
                aria-label={mapExpanded ? "지도 축소" : "지도 전체화면"}
                onClick={toggleMapExpanded}
              >
                <span
                  key={mapExpanded ? "collapse" : "expand"}
                  className={`slam-fullscreen-swap${morphSnap ? " is-live" : ""}`}
                >
                  {mapExpanded ? (
                    <Minimize2 size={18} strokeWidth={2.4} />
                  ) : (
                    <Maximize2 size={18} strokeWidth={2.4} />
                  )}
                  <span>{mapExpanded ? "축소" : "전체화면"}</span>
                </span>
              </button>
            </div>
            {/* 그리는 중 사용법·실패만 잠깐. */}
            {drawingHint ? (
              <p className="slam-hint slam-flash">{drawingHint}</p>
            ) : flashError ? (
              <p className="slam-hint slam-flash warn" role="alert">{flashError}</p>
            ) : null}
          </div>
        </div>
      </article>

    </section>
  );
}

export default MovePage;
