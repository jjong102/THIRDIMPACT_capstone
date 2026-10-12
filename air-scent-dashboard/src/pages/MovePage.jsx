import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Crosshair,
  Move,
  Pencil,
  RefreshCw,
  Navigation,
  Bot,
  LocateFixed,
  Wind,
  SprayCan,
  Activity,
  CircleDot,
  House,
  Check,
  X,
  Square,
} from "lucide-react";

import {
  DEFAULT_WARDS,
  MAX_WARDS,
  loadWards,
  saveWards,
  wardDisplayName,
} from "../utils/wardStorage";
import useGdmRobotPose from "../hooks/useGdmRobotPose";
import {
  GDM_FLOORPLAN_URL,
  computeContainLayout,
  containerPointToMapPercent,
  fetchGdmRooms,
  mapPercentToContainer,
  mapPercentToWorldMeters,
  poseToMapPercent,
  roomsToWards,
  sendInitialPose,
  sendNavGoal,
  cancelNavGoal,
  yawFromRobotToMapPercent,
  yawToCssDeg,
  yawToDisplayDeg,
} from "../services/gdmMap";
import { getScentVisual, readingForWard, isKitchenWard } from "../utils/scentVisual";
import useMoveUiSync from "../hooks/useMoveUiSync";
import { fetchMoveUi, putMoveUi, stopSharedFragranceSpray } from "../services/moveUi";
import { announceMoveTo, announceScentMission } from "../utils/robotAnnounce";
import {
  captureScentReturnPose,
  clearScentReturn,
} from "../utils/scentReturn";
import "./MovePage.css";

const GDM_SYNC_KEY = "air-scent-wards-gdm-synced";

function MovePage({
  data,
  mqttAirQuality,
  airPurifierOn = false,
  fragranceOn = false,
  fragranceDiffusing = false,
  fragranceSending = false,
  onStopFragrance,
}) {
  const mapRef = useRef(null);
  const imgRef = useRef(null);
  const [wards, setWards] = useState(loadWards);
  const [savedWards, setSavedWards] = useState(loadWards);
  const [selectedId, setSelectedId] = useState(
    () => loadWards()[0]?.id ?? DEFAULT_WARDS[0]?.id ?? null
  );
  const [mode, setMode] = useState("select");
  const [mapReady, setMapReady] = useState(false);
  const [mapFailed, setMapFailed] = useState(false);
  const [mapLayout, setMapLayout] = useState(null);
  const [moveStatus, setMoveStatus] = useState("대기");
  const [isMoving, setIsMoving] = useState(false);
  const [isNavigating, setIsNavigating] = useState(false);
  const [isStopping, setIsStopping] = useState(false);
  const [sharedSpraying, setSharedSpraying] = useState(false);
  const [editingName, setEditingName] = useState(false);
  const [roomPolys, setRoomPolys] = useState([]);
  const [gdmImporting, setGdmImporting] = useState(false);
  const [draggingId, setDraggingId] = useState(null);
  const dragRef = useRef(null);
  const [mapMeta, setMapMeta] = useState(null);
  const [dropPin, setDropPin] = useState(null);
  const [navGoal, setNavGoal] = useState(null);
  const [poseDragging, setPoseDragging] = useState(false);
  const [poseEstimating, setPoseEstimating] = useState(false);
  const [locStatus, setLocStatus] = useState(null);
  const poseDragRef = useRef(null);
  const navEpochRef = useRef(0);
  const previousSelectedIdRef = useRef(null);

  const { pose: gdmPose, status: gdmStatus } = useGdmRobotPose(true);

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
    draggingId,
    poseDragging,
  });

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

  const updateLayout = useCallback(() => {
    const container = mapRef.current;
    const img = imgRef.current;
    if (!container || !img?.naturalWidth) return;

    setMapLayout(
      computeContainLayout(
        container.clientWidth,
        container.clientHeight,
        img.naturalWidth,
        img.naturalHeight
      )
    );
  }, []);

  useEffect(() => {
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

      // 최초 1회만 GDM 좌표로 집 2곳 세팅 (다른 PC에 이미 있으면 덮어쓰지 않음)
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

        // 기존 2집을 우선 유지 (GDM id가 달라도 위치 보존)
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
    importGdmRooms();
  }, [importGdmRooms]);

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
      if (!el) return null;
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
    const yaw = dragPx < 16 ? drag.startYaw : Math.atan2(-dy, dx);

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

  const handleMapPointer = (event) => {
    if (isMoving || draggingId || poseDragging) return;
    if (!poseEstimating && locStatus === "not_localized") return;
    if (!poseEstimating && event.target.closest(".slam-ward")) return;
    if (event.target.closest(".slam-pin-actions")) return;

    if (mode === "move" && !poseEstimating) {
      setSelectedId(null);
      setEditingName(false);
      return;
    }

    const point = placePoint(event.clientX, event.clientY);
    if (!point) return;
    event.preventDefault();
    setSelectedId(null);
    setEditingName(false);

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
    setMoveStatus(
      poseEstimating
        ? "드래그해서 방향을 정하세요"
        : "드래그해서 이동 방향을 정하세요"
    );
    try {
      event.currentTarget.setPointerCapture?.(event.pointerId);
    } catch {
      /* ignore */
    }
  };

  const handleWardSelect = (wardId, event) => {
    if (isMoving || poseEstimating || locStatus === "not_localized") return;
    event.stopPropagation();

    setSelectedId(wardId);
    setDropPin(null);
    setEditingName(false);

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

  const renameSelected = (name) => {
    if (!selectedId) return;
    setWards((prev) =>
      prev.map((ward) =>
        ward.id === selectedId ? { ...ward, name: name.slice(0, 12) } : ward
      )
    );
  };

  const saveSelectedPin = () => {
    if (!selectedId || isMoving) return;
    commitWards(wards);
    setMode("select");
    setMoveStatus("핀 위치 저장됨");
  };

  const cancelSelectedPin = () => {
    if (!selectedId || isMoving) return;

    const saved = savedWards.find((ward) => ward.id === selectedId);
    if (!saved) {
      setMode("select");
      setEditingName(false);
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
    setEditingName(false);
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
        setMoveStatus(
          `위치 추정 (${world.x.toFixed(1)}, ${world.y.toFixed(1)}, ${yawToDisplayDeg(yaw)}°)`
        );
        setPoseEstimating(false);
        setDropPin(null);
        if (previousSelectedIdRef.current) {
          setSelectedId(previousSelectedIdRef.current);
        }
        window.setTimeout(() => {
          setMoveStatus("위치 추정 완료");
          window.setTimeout(() => setMoveStatus("대기"), 1800);
        }, 900);
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
    if (isMoving || isStopping || poseEstimating) return;
    if (locStatus === "not_localized") return;
    const target = resolveTargetPercent();
    if (!target) return;
    if (!dropPin && selectedWard && selectedPinDirty) return;

    const yaw = yawFromRobotToMapPercent(
      gdmPose,
      target.x,
      target.y,
      mapMeta,
      { reverse: Boolean(target.reverse) },
    );

    void (async () => {
      const isScent = Boolean(selectedWard && !dropPin && !poseEstimating);
      if (isScent) {
        await captureScentReturnPose(gdmPose, mapMeta);
      } else {
        clearScentReturn();
      }
      try {
        await putMoveUi({
          selectedId: isScent ? selectedWard.id : null,
          dropPin: dropPin
            ? {
                x: Number(dropPin.x),
                y: Number(dropPin.y),
                yaw: Number(dropPin.yaw) || 0,
                dragPx: Number(dropPin.dragPx) || 0,
              }
            : null,
          navGoal: { x: Number(target.x), y: Number(target.y), yaw },
          isNavigating: true,
          scentMission: isScent,
          mode: "select",
        });
      } catch {
        // nav goal still goes out
      }
      await publishAtMapPercent(
        "goal",
        target.x,
        target.y,
        target.label,
        yaw,
      );
    })();
  };

  const stopMove = useCallback(async () => {
    if (isStopping) return;
    navEpochRef.current += 1;
    setIsStopping(true);
    setMoveStatus("정지 중");
    try {
      await cancelNavGoal();
      clearScentReturn();
      setIsNavigating(false);
      setIsMoving(false);
      try {
        await putMoveUi({ isNavigating: false, scentMission: false });
      } catch {
        // cancel already applied
      }
      setMoveStatus("정지");
      window.setTimeout(() => {
        setMoveStatus((current) => (current === "정지" ? "대기" : current));
      }, 1800);
    } catch (error) {
      setMoveStatus(error?.message || "정지 실패");
    } finally {
      setIsStopping(false);
    }
  }, [isStopping]);

  const beginPoseEstimate = useCallback(() => {
    if (isMoving || isStopping || poseDragging || isNavigating) return;
    previousSelectedIdRef.current = selectedId;
    setPoseEstimating(true);
    setSelectedId(null);
    setEditingName(false);
    setDropPin(null);
    setMode("select");
    setMoveStatus("지도를 누른 채 드래그해 방향 화살표를 만드세요");
  }, [isMoving, isNavigating, isStopping, poseDragging, selectedId]);

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

  const notLocalized = locStatus === "not_localized";
  const modeHint = poseEstimating
    ? dropPin
      ? "완료를 누르면 이 위치와 방향으로 추정합니다"
      : "지도를 누른 채 드래그하면 방향 화살표가 생깁니다"
    : notLocalized
      ? "위치 추정이 필요합니다. 위치 추정만 할 수 있습니다"
      : {
          select: "지도를 눌러 이동 위치를 지정하거나, 집을 선택하세요",
          move: "집을 드래그해 위치 수정",
        }[mode];

  const deviceRunning = airPurifierOn || fragranceOn || fragranceDiffusing;
  let deviceLabel = "대기";
  let deviceTone = "idle";
  if (airPurifierOn && (fragranceOn || fragranceDiffusing)) {
    deviceLabel = "공기·향기 가동";
    deviceTone = "both";
  } else if (airPurifierOn) {
    deviceLabel = "공기청정 가동";
    deviceTone = "air";
  } else if (fragranceDiffusing) {
    deviceLabel = "발향 중";
    deviceTone = "scent";
  } else if (fragranceOn) {
    deviceLabel = "향기 분사";
    deviceTone = "scent";
  }

  const DeviceIcon =
    deviceTone === "air"
      ? Wind
      : deviceTone === "scent"
        ? SprayCan
        : deviceTone === "both"
          ? Activity
          : CircleDot;

  const selectedScreen = selectedWard
    ? mapPercentToContainer(selectedWard.x, selectedWard.y, mapLayout)
    : null;
  const dropPinScreen = dropPin
    ? mapPercentToContainer(dropPin.x, dropPin.y, mapLayout)
    : null;
  const navGoalScreen = navGoal
    ? mapPercentToContainer(navGoal.x, navGoal.y, mapLayout)
    : null;
  const pathGoalScreen = isNavigating
    ? navGoalScreen || dropPinScreen || selectedScreen
    : null;
  const hasDestination = Boolean(poseEstimating || dropPin || selectedWard);
  const scentMission = Boolean(selectedWard && !dropPin && !poseEstimating);
  const goBusyLabel = isMoving ? "전송 중…" : null;
  const goLabel = goBusyLabel ?? (scentMission ? "발향청정" : "위치 이동");
  const GoIcon = scentMission ? Wind : Navigation;
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
          deg: yawToCssDeg(dropPin.yaw ?? 0),
        }
      : null;

  return (
    <section className="move-page">
      <article className="slam-stage">
        <header className="slam-toolbar">
          <div className="slam-toolbar-left">
            <div className={`slam-live-chip${notLocalized ? " warn" : ""}`}>
              <span className="slam-live-dot" aria-hidden="true" />
              {gdmStatus.map ? `GDM · ${gdmStatus.map}` : "SLAM 지도"}
              {notLocalized
                ? " · 위치 추정 안 됨"
                : robotMapPos?.live
                  ? ` · ${robotMapPos.source === "gdm_web" ? "GDM pose" : "AMCL"}`
                  : " · 위치 없음"}
            </div>
            <p className={`slam-hint${notLocalized && !poseEstimating ? " warn" : ""}`}>
              {modeHint}
            </p>
          </div>

          <div className="slam-toolbar-actions">
            <button
              type="button"
              className="slam-mode-btn"
              onClick={() => importGdmRooms()}
              disabled={gdmImporting}
              title="GDM 방 윤곽을 갱신합니다 (저장한 핀 위치는 유지)"
            >
              <RefreshCw size={16} className={gdmImporting ? "spin" : ""} />
              GDM 방 불러오기
            </button>

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
            </div>
          </div>
        </header>

        <div className="slam-status-bar">
          <div className="slam-status-item">
            <LocateFixed size={18} />
            <span>
              로봇 ·{" "}
              <strong>
                {liveRobotMap?.live
                  ? `${gdmPose.x_m?.toFixed?.(2) ?? "—"} m`
                  : data.robotLocation}
              </strong>
              {liveRobotMap?.live ? (
                <em className="slam-live-tag">LIVE</em>
              ) : (
                <em className="slam-offline-tag">위치 없음</em>
              )}
            </span>
          </div>
          <div className={`slam-status-item ${isMoving || isNavigating ? "busy" : ""}`}>
            <Navigation size={18} />
            <span>
              이동 <strong>{moveStatus}</strong>
            </span>
          </div>
          <div
            className={`slam-status-item device ${deviceTone}${deviceRunning ? " running" : ""}`}
          >
            <DeviceIcon size={18} />
            <span>
              상태 <strong>{deviceLabel}</strong>
            </span>
          </div>
        </div>

        <div
          ref={mapRef}
          className={`slam-map ${
            poseEstimating
              ? "posing estimating-pose"
              : mode === "move"
                ? "relocating"
                : "posing"
          }${poseDragging ? " dragging-pose" : ""}`}
          onPointerDown={handleMapPointer}
          role="application"
          aria-label="GDM SLAM 지도"
        >
          <div className="slam-map-surface" aria-hidden="true">
            <div className="slam-map-base" />
            <div className="slam-map-grid" />
            {!mapFailed && (
              <img
                ref={imgRef}
                className={`slam-map-image ${mapReady ? "ready" : ""}`}
                src={`${GDM_FLOORPLAN_URL}?t=${gdmStatus.map ?? "map"}`}
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
                  ? "GDM floor_plan PNG를 찾을 수 없습니다 (gdm-bridge 확인)"
                  : "GDM 지도 불러오는 중…"}
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

          {!robotScreen ? (
            <div className="slam-pose-missing" aria-live="polite">
              <LocateFixed size={16} strokeWidth={2.4} />
              <span>로봇 위치를 받지 못했습니다</span>
            </div>
          ) : null}

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
                  transform: `rotate(${yawToCssDeg(robotMapPos?.yaw ?? 0)}deg)`,
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

                <button
                  type="button"
                  className={`slam-ward ${active ? "selected" : ""} ${mode === "move" && !poseEstimating ? "movable" : ""} ${draggingId === ward.id ? "dragging" : ""} ${
                    active && selectedPinDirty ? "dirty" : ""
                  }`}
                  onPointerDown={(event) => handleWardSelect(ward.id, event)}
                  disabled={isMoving || poseEstimating || notLocalized}
                  aria-label={`${ward.name} 위치`}
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
                    {hasScent ? ` · ${visual.short}` : ""}
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
        </div>
      </article>

      <aside className="slam-side-stack">
        <div className="slam-side">
          <div className="slam-side-head">
            <p className="section-label">집 위치</p>
            <strong>2곳</strong>
          </div>

          <div className="slam-home-slots" role="listbox" aria-label="집 위치">
            {Array.from({ length: MAX_WARDS }, (_, index) => {
              const ward = wards[index] ?? null;
              const slotLabel = `집 ${index + 1}`;

              if (!ward) {
                return (
                  <div
                    key={`slot-${index}`}
                    className="slam-home-card empty"
                    role="option"
                    aria-selected={false}
                  >
                    <div className="slam-home-card-icon">
                      <House size={22} strokeWidth={2.2} />
                    </div>
                    <div className="slam-home-card-body">
                      <strong>{slotLabel}</strong>
                      <em>아직 설정되지 않음</em>
                    </div>
                  </div>
                );
              }

              const active = ward.id === selectedId;
              const saved = savedWards.find((item) => item.id === ward.id);
              const dirty = !saved
                ? true
                : saved.x !== ward.x ||
                  saved.y !== ward.y ||
                  saved.name !== ward.name;
              const visual = wardVisuals[ward.id];
              const statusLabel = dirty
                ? "저장 필요"
                : active
                  ? "선택됨"
                  : "준비됨";

              return (
                <button
                  key={ward.id}
                  type="button"
                  role="option"
                  aria-selected={active}
                  className={`slam-home-card ${active ? "selected" : ""} ${dirty ? "dirty" : ""}`}
                  onClick={() => {
                    if (isMoving || poseEstimating || notLocalized) return;
                    setSelectedId(ward.id);
                    setDropPin(null);
                    setMode("select");
                    setEditingName(false);
                  }}
                  disabled={(isMoving && !active) || poseEstimating || notLocalized}
                >
                  <div className="slam-home-card-icon" aria-hidden="true">
                    <House size={22} strokeWidth={2.2} />
                  </div>
                  <div className="slam-home-card-body">
                    <div className="slam-home-card-title">
                      <strong>{wardDisplayName(ward)}</strong>
                      <span className="slam-home-card-badge">{slotLabel}</span>
                    </div>
                    <em>
                      {dirty
                        ? "위치를 저장해 주세요"
                        : visual?.tone &&
                            visual.tone !== "waiting" &&
                            visual.tone !== "offline"
                          ? visual.detail
                          : `좌표 ${ward.x.toFixed(0)}, ${ward.y.toFixed(0)}`}
                    </em>
                  </div>
                  <span className={`slam-home-card-status ${dirty ? "warn" : active ? "on" : ""}`}>
                    {statusLabel}
                  </span>
                </button>
              );
            })}
          </div>
        </div>

        <div className={`slam-destination ${hasDestination ? "" : "empty"}`}>
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
                  {isMoving ? "전송 중…" : "완료"}
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
                {dropPin ? (
                  <button
                    type="button"
                    className="slam-icon-btn"
                    onClick={() => {
                      if (isMoving) return;
                      setDropPin(null);
                      setMoveStatus("대기");
                    }}
                    disabled={isMoving}
                    aria-label="핀 제거"
                  >
                    <X size={16} />
                  </button>
                ) : selectedWard ? (
                  <button
                    type="button"
                    className="slam-icon-btn"
                    onClick={() => setEditingName((prev) => !prev)}
                    disabled={isMoving}
                    aria-label="이름 수정"
                  >
                    <Pencil size={16} />
                  </button>
                ) : null}
              </div>

              {selectedWard && !dropPin && editingName ? (
                <input
                  className="slam-name-input"
                  value={selectedWard.name}
                  autoFocus
                  maxLength={12}
                  onChange={(event) => renameSelected(event.target.value)}
                  onBlur={() => setEditingName(false)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") setEditingName(false);
                  }}
                />
              ) : (
                <h2>
                  {dropPin
                    ? "클릭한 위치"
                    : selectedWard
                      ? wardDisplayName(selectedWard)
                      : "핀 없음"}
                </h2>
              )}

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
                  "지도를 눌러 목적지를 지정하거나, 집을 선택하세요"
                )}
              </p>

              <div className={`slam-selected-actions dual-pose${scentSpraying ? " with-stop" : ""}`}>
                <button
                  type="button"
                  className={`slam-pose-btn${notLocalized ? " needed" : ""}`}
                  onClick={beginPoseEstimate}
                  disabled={isMoving || isStopping || isNavigating}
                >
                  <LocateFixed size={18} />
                  위치 추정
                </button>
                <button
                  type="button"
                  className={`slam-go-btn ${isNavigating ? "is-stop" : ""}${isMoving && !isNavigating ? " busy" : ""}${!isNavigating && scentMission ? " scent-mission" : ""}${isStopping ? " busy" : ""}`}
                  onClick={isNavigating || isMoving ? stopMove : startMove}
                  disabled={
                    isStopping ||
                    poseDragging ||
                    (!(isNavigating || isMoving) &&
                      (notLocalized ||
                        (!dropPin && !selectedWard) ||
                        (!dropPin && selectedPinDirty)))
                  }
                >
                  {isNavigating ? (
                    <>
                      <Square size={18} strokeWidth={2.4} fill="currentColor" />
                      {isStopping ? "정지 중…" : "정지"}
                    </>
                  ) : (
                    <>
                      <GoIcon size={18} />
                      {isMoving ? "전송 중…" : goLabel}
                    </>
                  )}
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
          )}
        </div>
      </aside>
    </section>
  );
}

export default MovePage;
