import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import {
  Crosshair,
  Move,
  RefreshCw,
  Bot,
  LocateFixed,
  Wind,
  House,
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
  publishImpactCommand,
  saveTaughtZone,
  startGoalTeach,
  yawFromRobotToMapPercent,
  yawToDisplayDeg,
} from "../services/gdmMap";
import { getScentVisual, readingForWard, isKitchenWard } from "../utils/scentVisual";
import useMoveUiSync from "../hooks/useMoveUiSync";
import { fetchMoveUi, putMoveUi, stopSharedFragranceSpray } from "../services/moveUi";
import { announceMoveTo, announceScentMission } from "../utils/robotAnnounce";
import { scentLabel } from "../data/scentRecipes";
import AiRecommendPage from "./AiRecommendPage";
import MapScentPopover from "../components/MapScentPopover";
import MapZoneOverlay from "../components/MapZoneOverlay";
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
const SCENT_BURST_ANGLES = [0, 60, 120, 180, 240, 300];

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
  const [gdmImporting, setGdmImporting] = useState(false);
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
  const [poseEstimating, setPoseEstimating] = useState(false);
  const [locStatus, setLocStatus] = useState(null);
  const [missionPhase, setMissionPhase] = useState(null);
  const [manualControl, setManualControl] = useState(false);
  const [missionReady, setMissionReady] = useState(null);
  const [lastEvent, setLastEvent] = useState(null);
  const [localizationState, setLocalizationState] = useState(null);
  const [posePending, setPosePending] = useState(false);
  const [goalStep, setGoalStep] = useState("idle");
  const [goalBusy, setGoalBusy] = useState(false);
  const [zoneKind, setZoneKind] = useState(null);
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
    poseDragging,
  });

  useEffect(() => {
    if (!poseEstimating && dropPin) setDropPin(null);
  }, [poseEstimating, dropPin]);

  useEffect(() => {
    if (!posePending) return;
    if (localizationState === "READY") {
      setPosePending(false);
      setMoveStatus("위치 추정 완료");
    } else if (localizationState === "SEARCH_FAILED" || localizationState === "AMBIGUOUS") {
      setPosePending(false);
      setMoveStatus("위치 추정을 다시 해 주세요");
    }
  }, [localizationState, posePending]);

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

  const updateLayout = useCallback(() => {
    const container = mapRef.current;
    const img = imgRef.current;
    if (!container || !img?.naturalWidth) return;

    setMapLayout(
      computeContainLayout(
        container.clientWidth,
        container.clientHeight,
        img.naturalWidth,
        img.naturalHeight,
        mapRotation,
        mapZoom,
        mapCenterX,
        mapCenterY,
      )
    );
  }, [mapRotation, mapZoom, mapCenterX, mapCenterY]);

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
  const scentPanelWard = useMemo(
    () => wards.find((ward) => ward.id === scentPanelWardId) ?? null,
    [wards, scentPanelWardId]
  );

  useEffect(() => subscribeDeveloperMode(setDeveloperMode), []);

  useEffect(() => {
    if (mode === "move" || poseEstimating) {
      setScentPanelWardId(null);
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

    return map;
  }, [wards, mqttAirQuality, fallbackReading]);

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

  const endPoseDrag = useCallback(() => {
    poseDragRef.current = null;
    setPoseDragging(false);
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
      endPoseDrag();
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

  const handleWardSelect = (wardId, event) => {
    if (isMoving || poseEstimating || locStatus === "not_localized") return;
    event.stopPropagation();
    if (scentConversationOpen) return;

    setSelectedId(wardId);
    setDropPin(null);
    if (mode === "select") {
      setScentPanelWardId((current) => (current === wardId ? null : wardId));
    }

    // 위치 모드에서만 드래그로 이동
    if (mode === "move") {
      dragRef.current = { id: wardId, pointerId: event.pointerId };
      setDraggingId(wardId);
      try {
        event.currentTarget.setPointerCapture?.(event.pointerId);
      } catch {
        /* ignore */
      }
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
          if (label && label !== "핀") {
            announceScentMission(label);
          } else {
            announceMoveTo(label === "핀" ? "" : label);
          }
          return;
        }
        setPosePending(true);
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

  const startMove = () => {
    if (isMoving || isStopping || poseEstimating || manualControl) return;
    if (locStatus === "not_localized" || missionReady === false) return;

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
      setMoveStatus("목표로 복귀");
      if (isScent) {
        await captureScentReturnPose(gdmPose, mapMeta);
      } else {
        clearScentReturn();
      }
      try {
        await sendNavGoal({ x: 0, y: 0, yaw: 0, frame_id: "map" });
        await putMoveUi({
          selectedId: isScent ? selectedWard.id : selectedId,
          dropPin: null,
          ...(nextGoal ? { navGoal: nextGoal } : {}),
          isNavigating: true,
          scentMission: true,
          mode: "select",
        });
        setIsMoving(false);
        setMoveStatus("목표로 복귀 중");
        if (isScent) announceScentMission(selectedWard);
        else announceMoveTo("목표");
      } catch (error) {
        setIsMoving(false);
        setIsNavigating(false);
        setMoveStatus(error?.message || "목표 복귀 실패");
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
    if (isMoving || isStopping || poseDragging) return;
    previousSelectedIdRef.current = selectedId;
    setPoseEstimating(true);
    setSelectedId(null);
    setDropPin(null);
    setMode("select");
    setMoveStatus("지도를 누른 채 드래그해 방향 화살표를 만드세요");
  }, [isMoving, isStopping, poseDragging, selectedId]);

  const cancelPoseEstimate = useCallback(() => {
    if (isMoving) return;
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

  const beginGoalSetup = useCallback(async () => {
    if (goalBusy || goalStep === "launching") return;
    setGoalBusy(true);
    setGoalStep("launching");
    setMoveStatus("목표 설정 모드로 전환 중");
    try {
      await startGoalTeach();
      setGoalStep("save");
      setMoveStatus("조이스틱으로 목표 위치에 세운 뒤 저장하세요");
    } catch (error) {
      setGoalStep("idle");
      setMoveStatus(error?.message || "목표 설정 모드 전환 실패");
    } finally {
      setGoalBusy(false);
    }
  }, [goalBusy, goalStep]);

  const saveGoalPose = useCallback(async () => {
    if (goalBusy) return;
    setGoalBusy(true);
    setMoveStatus("목표 위치 저장 중");
    try {
      await publishImpactCommand("save_goal");
    } catch (error) {
      setMoveStatus(error?.message || "목표 위치 저장 실패");
    } finally {
      setGoalBusy(false);
    }
  }, [goalBusy]);

  const commitZone = useCallback(async () => {
    const box = zoneKind ? zoneBoxes[zoneKind] : null;
    if (!zoneKind || !box || goalBusy) return;
    const a = mapPercentToWorldMeters(box.x1, box.y1, mapMeta);
    const b = mapPercentToWorldMeters(box.x2, box.y2, mapMeta);
    if (!a || !b) {
      setMoveStatus("지도 좌표를 계산하지 못했습니다");
      return;
    }
    const width = Math.abs(b.x - a.x);
    const height = Math.abs(b.y - a.y);
    if (width < 0.3 || height < 0.3) {
      setMoveStatus("구역을 더 크게 드래그하세요");
      return;
    }
    setGoalBusy(true);
    const zoneName = zoneKind === "region" ? "위치 제한 구역" : "한계 구역";
    try {
      const before = await fetchMoveUi();
      const previousDetail = before?.robotDetail || "";
      setMoveStatus(`${zoneName}을 주행 젯슨에 보내는 중`);
      await saveTaughtZone({
        kind: zoneKind,
        x: (a.x + b.x) / 2,
        y: (a.y + b.y) / 2,
        yaw: 0,
        width,
        height,
      });
      let note = `${zoneName}을 주행 젯슨에 보냈습니다`;
      for (let attempt = 0; attempt < 12; attempt += 1) {
        await new Promise((resolve) => window.setTimeout(resolve, 400));
        const state = await fetchMoveUi();
        const detail = state?.robotDetail || "";
        if (!detail || detail === previousDetail) continue;
        if (detail.includes("저장") || detail.includes("구간") || detail.includes("포함") || detail.includes("목표")) {
          note = detail;
          break;
        }
      }
      setMoveStatus(note);
      setZoneKind(null);
    } catch (error) {
      setMoveStatus(error?.message || "구역 저장 실패");
    } finally {
      setGoalBusy(false);
    }
  }, [goalBusy, mapMeta, zoneBoxes, zoneKind]);

  const onZoneButton = useCallback(
    (kind) => {
      if (zoneKind === kind) {
        if (zoneBoxes[kind]) void commitZone();
        else setMoveStatus("지도를 드래그해서 구역을 먼저 그리세요");
        return;
      }
      setZoneKind(kind);
      setMoveStatus(
        kind === "region"
          ? "지도를 드래그해서 위치 제한 구역을 그리세요"
          : "지도를 드래그해서 한계 구역을 그리세요. 한계는 목표를 포함해야 합니다"
      );
    },
    [commitZone, zoneBoxes, zoneKind]
  );

  const zoneButtonLabel = (kind) => {
    if (zoneKind === kind && zoneBoxes[kind]) {
      return kind === "region" ? "위치 제한 저장" : "한계 구역 저장";
    }
    return kind === "region" ? "위치 제한 구역" : "한계 구역";
  };

  const locatingNow = ["VERIFYING", "SEARCHING", "LOCAL_RECHECK"].includes(localizationState);
  const poseBusy = posePending || locatingNow;
  const poseDone =
    !poseEstimating &&
    !poseBusy &&
    (localizationState === "READY" || locStatus === "localized");
  const poseLabel = poseEstimating
    ? "위치 추정 저장"
    : poseBusy
      ? "위치 추정 중"
      : poseDone
        ? "위치 추정 완료"
        : "위치 추정 하기";
  const goalLabel =
    goalStep === "launching"
      ? "전환 중…"
      : goalStep === "save"
        ? "목표 위치 저장"
        : goalStep === "zones"
          ? "목표 저장됨"
          : "목표 위치 설정";
  const notLocalized = locStatus === "not_localized" && !poseDone;
  const notReady = missionReady === false;
  const goBlocked = notLocalized || notReady || manualControl;
  const modeHint = zoneKind
    ? zoneKind === "region"
      ? "지도를 드래그해서 위치 제한 구역을 그린 뒤 저장하세요"
      : "지도를 드래그해서 한계 구역을 그린 뒤 저장하세요. 한계는 목표를 포함해야 합니다"
    : poseEstimating
    ? dropPin
      ? "위치 추정 저장을 누르면 이 화살표로 추정을 시작합니다"
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

  const placeLabel = manualControl
    ? "수동"
    : notLocalized
      ? "위치 추정 필요"
      : notReady
        ? "준비 안 됨"
        : liveRobotMap?.live
          ? `${gdmPose.x_m?.toFixed?.(1) ?? "—"} m`
          : "위치 없음";
  const showMoveNote = Boolean(moveStatus && moveStatus !== "대기" && !poseEstimating);
  const liveNote = showMoveNote ? moveStatus : lastEvent || "";
  const showModeHint =
    poseEstimating ||
    Boolean(zoneKind) ||
    notLocalized ||
    notReady ||
    manualControl ||
    missionPhase === "setup";

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
          length: Math.max(
            64,
            Math.min(180, Number(dropPin.dragPx) || 72)
          ),
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
                {wards.map((ward) => {
                  const visual = wardVisuals[ward.id];
                  if (
                    !visual ||
                    visual.tone === "waiting" ||
                    visual.tone === "offline"
                  ) {
                    return null;
                  }
                  return (
                    <radialGradient
                      key={`grad-${ward.id}`}
                      id={`scent-grad-${ward.id}`}
                      cx="50%"
                      cy="50%"
                      r="50%"
                    >
                      <stop offset="0%" stopColor={visual.color} stopOpacity="0.55" />
                      <stop offset="45%" stopColor={visual.color} stopOpacity="0.22" />
                      <stop offset="100%" stopColor={visual.color} stopOpacity="0" />
                    </radialGradient>
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

              {wards.map((ward) => {
                const visual = wardVisuals[ward.id];
                const hasScent =
                  visual &&
                  visual.tone !== "waiting" &&
                  visual.tone !== "offline";
                if (!hasScent) return null;

                const screen = mapPercentToContainer(ward.x, ward.y, mapLayout);
                const cx = (screen.x / 100) * mapLayout.containerW;
                const cy = (screen.y / 100) * mapLayout.containerH;
                // 맵 대비 작은 반경 — 와드 주변만
                const radius = Math.min(
                  200,
                  Math.max(
                    130,
                    Math.min(mapLayout.width, mapLayout.height) * 0.32
                  )
                );
                const hasClip = roomPolys.some((room) => room.id === ward.id);

                return (
                  <g
                    key={`scent-${ward.id}`}
                    clipPath={
                      hasClip ? `url(#ward-clip-${ward.id})` : undefined
                    }
                  >
                    <circle
                      className="slam-local-scent"
                      cx={cx}
                      cy={cy}
                      r={radius}
                      fill={`url(#scent-grad-${ward.id})`}
                    />
                    <circle
                      className="slam-local-scent-core"
                      cx={cx}
                      cy={cy}
                      r={radius * 0.42}
                      fill={visual.glow}
                    />
                  </g>
                );
              })}
            </svg>
          )}

          <MapZoneOverlay mapLayout={mapLayout} mapMeta={mapMeta} />

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
                  markerWidth="8"
                  markerHeight="8"
                  refX="6"
                  refY="4"
                  orient="auto"
                >
                  <path className="slam-pose-head" d="M0,0 L8,4 L0,8 Z" />
                </marker>
              </defs>
              <g
                className={`slam-pose-arrow${poseDragging ? " dragging" : ""}`}
                transform={`translate(${poseArrow.cx} ${poseArrow.cy}) rotate(${poseArrow.deg})`}
              >
                <circle className="slam-pose-origin" r="8" />
                <line
                  className="slam-pose-shaft"
                  x1="10"
                  y1="0"
                  x2={poseArrow.length}
                  y2="0"
                  markerEnd="url(#slam-pose-head)"
                />
              </g>
            </svg>
          ) : null}

          {navGoalScreen && !dropPinScreen ? (
            <div
              className="slam-nav-goal"
              style={{ left: `${navGoalScreen.x}%`, top: `${navGoalScreen.y}%` }}
              title="Nav2 목표"
            >
              <Crosshair size={16} strokeWidth={2.6} />
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

          {wards.map((ward) => {
            const active = ward.id === selectedId;
            const screen = mapPercentToContainer(ward.x, ward.y, mapLayout);
            const visual = wardVisuals[ward.id];
            const hasScent =
              visual &&
              visual.tone !== "waiting" &&
              visual.tone !== "offline";
            const scentUnset = Boolean(wardScents?.[ward.id]?.unset);
            const scentName = scentUnset
              ? "향기미설정"
              : scentLabel(
                  wardScents?.[ward.id]?.fragrance || data?.currentFragrance || ""
                );
            return (
              <div
                key={ward.id}
                className="slam-ward-wrap"
                style={{ left: `${screen.x}%`, top: `${screen.y}%` }}
              >
                <span className="slam-ward-point" aria-hidden="true">
                  <i />
                </span>

                {hasScent && (
                  <div
                    className={`slam-ward-aura tone-${visual.tone}`}
                    style={{
                      ["--aura-glow"]: visual.glow,
                      ["--aura-soft"]: visual.soft,
                      ["--aura-color"]: visual.color,
                    }}
                    aria-hidden="true"
                  >
                    <span className="slam-ward-aura-ring" />
                    <span className="slam-ward-aura-ring delay" />
                  </div>
                )}
                {hasScent && (
                  <div className="slam-scent-burst" aria-hidden="true">
                    {SCENT_BURST_ANGLES.map((angle, index) => (
                      <span
                        key={angle}
                        className="slam-scent-burst-emoji"
                        style={{
                          "--burst-angle": `${angle}deg`,
                          "--burst-delay": `${index * 0.42}s`,
                        }}
                      >
                        {visual.emoji}
                      </span>
                    ))}
                  </div>
                )}

                <button
                  type="button"
                  data-ward-anchor={ward.id}
                  className={`slam-ward ${active ? "selected" : ""} ${mode === "move" && !poseEstimating ? "movable" : ""} ${draggingId === ward.id ? "dragging" : ""} ${
                    active && selectedPinDirty ? "dirty" : ""
                  }`}
                  onPointerDown={(event) => handleWardSelect(ward.id, event)}
                  disabled={isMoving || poseEstimating || notLocalized}
                  aria-label={`${wardDisplayName(ward)}${scentName ? ` ${scentName}` : ""} 위치`}
                  aria-pressed={active}
                >
                  <span className="slam-ward-pin" aria-hidden="true">
                    <span className="slam-ward-pin-head">
                      {hasScent ? (
                        <span className="slam-ward-emoji">{visual.emoji}</span>
                      ) : (
                        <House size={active ? 20 : 18} strokeWidth={2.4} />
                      )}
                    </span>
                    <span className="slam-ward-pin-tip" />
                  </span>
                  <span className="slam-ward-label">
                    {wardDisplayName(ward)}
                    {scentName ? (
                      <em className={`slam-ward-scent${scentUnset ? " is-unset" : ""}`}>
                        · {scentName}
                      </em>
                    ) : null}
                  </span>
                </button>
              </div>
            );
          })}

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
                  {isMoving ? "전송 중…" : "위치 추정 저장"}
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
                <button
                  type="button"
                  className={`slam-pose-btn${poseDone ? " done" : ""}${poseBusy ? " busy" : ""}${!poseDone && !poseBusy ? " needed" : ""}`}
                  onClick={() => {
                    if (poseBusy) return;
                    beginPoseEstimate();
                  }}
                  disabled={isMoving || isStopping || poseBusy}
                >
                  <LocateFixed size={18} />
                  {poseLabel}
                </button>
                <button
                  type="button"
                  className={`slam-goal-btn${goalStep === "zones" ? " done" : ""}${goalBusy ? " busy" : ""}`}
                  onClick={() => {
                    if (goalStep === "save") {
                      void saveGoalPose();
                      return;
                    }
                    if (goalStep === "idle") void beginGoalSetup();
                  }}
                  disabled={goalBusy || goalStep === "launching" || goalStep === "zones" || poseEstimating}
                >
                  <Crosshair size={18} />
                  {goalBusy && goalStep !== "zones" ? "처리 중…" : goalLabel}
                </button>
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
            </>
          )}
        </div>

          <div
            className="slam-map-tools"
            onPointerDown={(event) => event.stopPropagation()}
          >
            <div className="slam-map-tools-row">
              {placeLabel !== "위치 없음" ? (
                <div
                  className={`slam-live-chip${notLocalized || !robotScreen ? " warn" : ""}`}
                  title={
                    robotMap.map?.name
                      ? `주행 맵 · ${robotMap.map.name}`
                      : gdmStatus.map
                        ? `GDM · ${gdmStatus.map}`
                        : "SLAM 지도"
                  }
                >
                  <span className="slam-live-dot" aria-hidden="true" />
                  <span>{placeLabel}</span>
                  {liveNote ? (
                    <span className="slam-live-note">{liveNote}</span>
                  ) : null}
                </div>
              ) : null}
              <button
                type="button"
                className="slam-mode-btn"
                onClick={() => importGdmRooms()}
                disabled={gdmImporting || Boolean(robotMap.meta)}
                title={
                  robotMap.meta
                    ? "주행 맵에는 GDM 방 윤곽이 없습니다"
                    : "GDM 방 윤곽을 갱신합니다 (저장한 핀 위치는 유지)"
                }
              >
                <RefreshCw size={16} className={gdmImporting ? "spin" : ""} />
                GDM 방 불러오기
              </button>
              {missionPhase === "setup" ? (
                <div className="slam-mode-group" role="group" aria-label="설정 저장">
                  <button
                    type="button"
                    className="slam-mode-btn"
                    onClick={() => {
                      setMoveStatus("목표 저장");
                      void publishImpactCommand("save_goal").catch((error) => {
                        setMoveStatus(error?.message || "목표 저장 실패");
                      });
                    }}
                  >
                    목표 저장
                  </button>
                  <button
                    type="button"
                    className="slam-mode-btn"
                    onClick={() => {
                      setMoveStatus("한계 저장");
                      void publishImpactCommand("save_boundary").catch((error) => {
                        setMoveStatus(error?.message || "한계 저장 실패");
                      });
                    }}
                  >
                    한계 저장
                  </button>
                  <button
                    type="button"
                    className="slam-mode-btn"
                    onClick={() => {
                      setMoveStatus("정찰 구간 저장");
                      void publishImpactCommand("save_region").catch((error) => {
                        setMoveStatus(error?.message || "정찰 구간 저장 실패");
                      });
                    }}
                  >
                    정찰 구간 저장
                  </button>
                </div>
              ) : null}
              <div className="slam-mode-group" role="tablist" aria-label="지도 모드">
                <button
                  type="button"
                  className={`slam-mode-btn ${mode === "select" ? "active" : ""}`}
                  onClick={() => setMode("select")}
                  disabled={isMoving || poseEstimating || notLocalized}
                >
                  <Crosshair size={16} />
                  선택
                </button>
                <button
                  type="button"
                  className={`slam-mode-btn ${mode === "move" ? "active" : ""}`}
                  onClick={() => setMode("move")}
                  disabled={isMoving || poseEstimating || notLocalized}
                >
                  <Move size={16} />
                  위치
                </button>
                <button
                  type="button"
                  className={`slam-mode-btn zone-region${zoneKind === "region" ? " active" : ""}`}
                  onClick={() => onZoneButton("region")}
                  disabled={goalBusy || poseEstimating}
                >
                  {zoneButtonLabel("region")}
                </button>
                <button
                  type="button"
                  className={`slam-mode-btn zone-boundary${zoneKind === "boundary" ? " active" : ""}`}
                  onClick={() => onZoneButton("boundary")}
                  disabled={goalBusy || poseEstimating}
                >
                  {zoneButtonLabel("boundary")}
                </button>
              </div>
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
            {!robotScreen ? (
              <p className="slam-pose-missing" aria-live="polite">
                <LocateFixed size={16} strokeWidth={2.4} />
                <span>로봇 위치를 받지 못했습니다</span>
              </p>
            ) : null}
            {showModeHint ? (
              <p className={`slam-hint${notLocalized && !poseEstimating ? " warn" : ""}`}>
                {modeHint}
              </p>
            ) : null}
          </div>
        </div>
      </article>

      {scentPanelWard ? (
        <MapScentPopover
          key={scentPanelWard.id}
          wardId={scentPanelWard.id}
          wide={scentPanelView === "chat" || scentPanelView === "text"}
          title="향 추천"
          onClose={() => setScentPanelWardId(null)}
        >
          {scentPanelView === "chat" ? (
            <AiRecommendPage
              key={devSeed || "voice"}
              recommendedFragrance={recommendedFragrance}
              recommendedReason={recommendedReason}
              data={data}
              mqttAirQuality={mqttAirQuality}
              fragranceCartridgeRemaining={fragranceCartridgeRemaining}
              lastDispenseAt={lastDispenseAt}
              targetWardName={wardDisplayName(scentPanelWard)}
              targetWardId={scentPanelWard.id}
              initialView="chat"
              seedPrompt={devSeed}
              onBack={() => setScentPanelView(developerMode ? "text" : "buttons")}
              onApplyRecommendation={onApplyFragrance}
            />
          ) : scentPanelView === "text" ? (
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
          ) : (
            <div className="map-pin-actions">
              <button
                type="button"
                className="map-pin-action scent"
                onClick={() => {
                  if (developerMode) {
                    setDevSeed("");
                    setScentPanelView("text");
                    return;
                  }
                  setDevSeed("");
                  setScentPanelView("chat");
                }}
              >
                <Sparkles size={16} />
                <span>향 추천</span>
              </button>
            </div>
          )}
        </MapScentPopover>
      ) : null}
    </section>
  );
}

export default MovePage;
