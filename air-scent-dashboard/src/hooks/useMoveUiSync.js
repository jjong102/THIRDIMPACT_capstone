import { useCallback, useEffect, useRef } from "react";

import { fetchMoveUi, putMoveUi } from "../services/moveUi";
import { MAX_WARDS, saveWards, wardDisplayName } from "../utils/wardStorage";

const POLL_MS = 1000;

function sameWards(a, b) {
  if (a === b) return true;
  if (!Array.isArray(a) || !Array.isArray(b) || a.length !== b.length) {
    return false;
  }
  return a.every(
    (ward, index) =>
      ward.id === b[index].id &&
      ward.name === b[index].name &&
      Number(ward.x) === Number(b[index].x) &&
      Number(ward.y) === Number(b[index].y)
  );
}

function samePin(a, b) {
  if (a === b) return true;
  if (!a || !b) return !a && !b;
  return (
    Number(a.x) === Number(b.x) &&
    Number(a.y) === Number(b.y) &&
    Number(a.yaw || 0) === Number(b.yaw || 0)
  );
}

function normalizeWards(list) {
  if (!Array.isArray(list)) return [];
  return list
    .map((ward) => ({
      id: String(ward?.id ?? ""),
      name: wardDisplayName(ward, String(ward?.name ?? "집")).slice(0, 12),
      x: Number(ward?.x),
      y: Number(ward?.y),
      ...(ward?.sensorId ? { sensorId: String(ward.sensorId) } : {}),
    }))
    .filter((ward) => ward.id && Number.isFinite(ward.x) && Number.isFinite(ward.y))
    .slice(0, MAX_WARDS);
}

function normalizePin(pin) {
  if (!pin || typeof pin !== "object") return null;
  const x = Number(pin.x);
  const y = Number(pin.y);
  if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
  return {
    x,
    y,
    yaw: Number(pin.yaw) || 0,
    dragPx: Number(pin.dragPx) || 0,
  };
}

export default function useMoveUiSync({
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
}) {
  const lastRevRef = useRef(-1);
  const applyingRemoteRef = useRef(false);
  const hydratedRef = useRef(false);
  const snapshotRef = useRef(null);

  snapshotRef.current = {
    wards,
    selectedId,
    dropPin,
    navGoal,
    isNavigating,
    mode,
    poseEstimating,
    locStatus,
    missionPhase,
    manualControl,
    missionReady,
    lastEvent,
    localizationState,
  };

  const rememberRev = useCallback((state) => {
    const rev = Number(state?.rev) || 0;
    if (rev > lastRevRef.current) {
      lastRevRef.current = rev;
    }
  }, []);

  const applyRemote = useCallback(
    (raw) => {
      const rev = Number(raw?.rev) || 0;
      if (rev <= 0 || rev <= lastRevRef.current) return false;

      lastRevRef.current = rev;
      applyingRemoteRef.current = false;

      const nextWards = normalizeWards(raw.wards);
      if (nextWards.length > 0 && !sameWards(nextWards, snapshotRef.current.wards)) {
        applyingRemoteRef.current = true;
        const snapshot = nextWards.map((ward) => ({ ...ward }));
        saveWards(snapshot);
        setWards(snapshot);
        setSavedWards(snapshot);
      }

      const nextPoseEstimating = Boolean(raw.poseEstimating);
      if (nextPoseEstimating !== Boolean(snapshotRef.current.poseEstimating)) {
        applyingRemoteRef.current = true;
        setPoseEstimating(nextPoseEstimating);
      }

      const nextSelected = raw.selectedId ? String(raw.selectedId) : null;
      if (nextPoseEstimating) {
        if (snapshotRef.current.selectedId) {
          applyingRemoteRef.current = true;
          setSelectedId(null);
        }
      } else if (nextSelected !== snapshotRef.current.selectedId) {
        applyingRemoteRef.current = true;
        setSelectedId(nextSelected);
      }

      const nextDrop = normalizePin(raw.dropPin);
      if (!samePin(nextDrop, snapshotRef.current.dropPin)) {
        applyingRemoteRef.current = true;
        setDropPin(nextDrop);
      }

      const nextGoal = normalizePin(raw.navGoal);
      if (!samePin(nextGoal, snapshotRef.current.navGoal)) {
        applyingRemoteRef.current = true;
        setNavGoal(nextGoal);
      }

      if (Boolean(raw.isNavigating) !== Boolean(snapshotRef.current.isNavigating)) {
        applyingRemoteRef.current = true;
        setIsNavigating(Boolean(raw.isNavigating));
      }

      const nextMode = raw.mode === "move" && !nextPoseEstimating ? "move" : "select";
      if (nextMode !== snapshotRef.current.mode) {
        applyingRemoteRef.current = true;
        setMode(nextMode);
      }

      const nextLoc =
        raw.locStatus === "not_localized" || raw.locStatus === "localized"
          ? raw.locStatus
          : null;
      if (nextLoc !== snapshotRef.current.locStatus) {
        applyingRemoteRef.current = true;
        setLocStatus(nextLoc);
      }

      const nextPhase = raw.missionPhase || null;
      if (nextPhase !== (snapshotRef.current.missionPhase || null)) {
        applyingRemoteRef.current = true;
        setMissionPhase?.(nextPhase);
      }

      const nextManual = Boolean(raw.manualControl);
      if (nextManual !== Boolean(snapshotRef.current.manualControl)) {
        applyingRemoteRef.current = true;
        setManualControl?.(nextManual);
      }

      const nextReady =
        typeof raw.missionReady === "boolean" ? raw.missionReady : null;
      if (nextReady !== (snapshotRef.current.missionReady ?? null)) {
        applyingRemoteRef.current = true;
        setMissionReady?.(nextReady);
      }

      const nextEvent = raw.lastEvent || null;
      if (nextEvent !== (snapshotRef.current.lastEvent || null)) {
        applyingRemoteRef.current = true;
        setLastEvent?.(nextEvent);
      }

      const nextLocState = raw.localizationState || null;
      if (nextLocState !== (snapshotRef.current.localizationState || null)) {
        applyingRemoteRef.current = true;
        setLocalizationState?.(nextLocState);
      }

      return true;
    },
    [
      setDropPin,
      setIsNavigating,
      setLastEvent,
      setLocalizationState,
      setLocStatus,
      setManualControl,
      setMissionPhase,
      setMissionReady,
      setMode,
      setNavGoal,
      setPoseEstimating,
      setSavedWards,
      setSelectedId,
      setWards,
    ]
  );

  const pushLocal = useCallback(async () => {
    const snap = snapshotRef.current;
    try {
      const result = await putMoveUi({
        wards: snap.wards,
        selectedId: snap.selectedId,
        dropPin: snap.dropPin,
        navGoal: snap.navGoal,
        isNavigating: snap.isNavigating,
        mode: snap.mode,
        poseEstimating: snap.poseEstimating,
      });
      rememberRev(result.state);
    } catch {
      // keep polling
    }
  }, [rememberRev]);

  useEffect(() => {
    let cancelled = false;

    async function ingest(raw) {
      if (!raw || cancelled) return;

      if (
        !hydratedRef.current &&
        snapshotRef.current.wards?.length &&
        ((Number(raw.rev) || 0) === 0 || !raw.wards?.length)
      ) {
        hydratedRef.current = true;
        try {
          const result = await putMoveUi(snapshotRef.current);
          if (!cancelled) rememberRev(result.state);
        } catch {
          if (!cancelled) lastRevRef.current = Number(raw.rev) || 0;
        }
        return;
      }

      if (draggingId || poseDragging) {
        if ((Number(raw.rev) || 0) > lastRevRef.current) {
          rememberRev(raw);
        }
        hydratedRef.current = true;
        return;
      }

      applyRemote(raw);
      hydratedRef.current = true;
    }

    async function poll() {
      try {
        const state = await fetchMoveUi();
        await ingest(state);
      } catch {
        if (!hydratedRef.current) {
          hydratedRef.current = true;
        }
      }
    }

    poll();
    const timer = window.setInterval(poll, POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [applyRemote, draggingId, poseDragging, rememberRev]);

  useEffect(() => {
    if (!hydratedRef.current) return undefined;
    if (applyingRemoteRef.current) {
      applyingRemoteRef.current = false;
      return undefined;
    }

    const delay = draggingId || poseDragging ? 150 : 0;
    const timer = window.setTimeout(() => {
      void pushLocal();
    }, delay);
    return () => window.clearTimeout(timer);
  }, [
    draggingId,
    dropPin,
    isNavigating,
    mode,
    navGoal,
    poseEstimating,
    poseDragging,
    pushLocal,
    selectedId,
    wards,
  ]);
}
