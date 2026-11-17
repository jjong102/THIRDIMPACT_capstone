import { useEffect, useRef, useSyncExternalStore } from "react";

import {
  fetchGdmRobotPose,
  fetchGdmStatus,
  fetchNavPose,
} from "../services/gdmMap";

const POSE_POLL_MS = 500;
const STATUS_POLL_MS = 5000;
const POS_EPS_M = 0.02;
const POS_EPS_PX = 1;
const YAW_EPS = Math.PI / 180;

const initialPose = {
  live: false,
  x_px: null,
  y_px: null,
  x_m: null,
  y_m: null,
  yaw: null,
  source: null,
  error: null,
};

const initialStatus = {
  hasFloorplan: false,
  hasRooms: false,
  map: null,
  robotPoseLive: false,
};

let poseState = initialPose;
let statusState = initialStatus;
let snapshot = { pose: poseState, status: statusState };
const listeners = new Set();
let poseTimer = null;
let statusTimer = null;
let generation = 0;
let poseSeq = 0;

function near(a, b, eps) {
  if (a == null && b == null) return true;
  if (a == null || b == null || !Number.isFinite(a) || !Number.isFinite(b)) {
    return a === b;
  }
  return Math.abs(a - b) <= eps;
}

function nearYaw(a, b) {
  if (a == null && b == null) return true;
  if (a == null || b == null || !Number.isFinite(a) || !Number.isFinite(b)) {
    return a === b;
  }
  let delta = Math.abs(a - b) % (Math.PI * 2);
  if (delta > Math.PI) delta = Math.PI * 2 - delta;
  return delta <= YAW_EPS;
}

function samePose(a, b) {
  return (
    a.live === b.live &&
    a.source === b.source &&
    a.error === b.error &&
    near(a.x_px, b.x_px, POS_EPS_PX) &&
    near(a.y_px, b.y_px, POS_EPS_PX) &&
    near(a.x_m, b.x_m, POS_EPS_M) &&
    near(a.y_m, b.y_m, POS_EPS_M) &&
    nearYaw(a.yaw, b.yaw)
  );
}

function sameStatus(a, b) {
  return (
    a.hasFloorplan === b.hasFloorplan &&
    a.hasRooms === b.hasRooms &&
    a.map === b.map &&
    a.robotPoseLive === b.robotPoseLive
  );
}

function publish(nextPose, nextStatus) {
  let changed = false;
  if (nextPose && !samePose(poseState, nextPose)) {
    poseState = nextPose;
    changed = true;
  }
  if (nextStatus && !sameStatus(statusState, nextStatus)) {
    statusState = nextStatus;
    changed = true;
  }
  if (!changed) return;
  snapshot = { pose: poseState, status: statusState };
  listeners.forEach((listener) => listener());
}

function getSnapshot() {
  return snapshot;
}

function getMapStamp() {
  return snapshot.status.map ?? null;
}

async function loadStatus() {
  const gen = generation;
  try {
    const data = await fetchGdmStatus();
    if (gen !== generation) return;
    publish(null, {
      hasFloorplan: Boolean(data.has_floorplan),
      hasRooms: Boolean(data.has_rooms),
      map: data.map ?? null,
      robotPoseLive: Boolean(data.robot_pose_live),
    });
  } catch {
    if (gen !== generation) return;
    publish(null, { ...statusState, hasFloorplan: false });
  }
}

async function loadPose() {
  const gen = generation;
  const seq = ++poseSeq;
  try {
    const gdm = await fetchGdmRobotPose();
    if (gen !== generation || seq !== poseSeq) return;

    if (gdm?.live && (gdm.x_px != null || gdm.x_m != null)) {
      publish({
        live: true,
        x_px: gdm.x_px ?? null,
        y_px: gdm.y_px ?? null,
        x_m: gdm.x_m ?? null,
        y_m: gdm.y_m ?? null,
        yaw: gdm.yaw ?? null,
        source: gdm.source ?? "gdm_web",
        error: null,
      });
      return;
    }

    const amcl = await fetchNavPose();
    if (gen !== generation || seq !== poseSeq) return;

    if (amcl?.live && amcl.x_m != null && amcl.y_m != null) {
      publish(
        {
          live: true,
          x_px: null,
          y_px: null,
          x_m: amcl.x_m,
          y_m: amcl.y_m,
          yaw: amcl.yaw ?? null,
          source: amcl.source ?? "amcl",
          error: null,
        },
        { ...statusState, robotPoseLive: true }
      );
      return;
    }

    publish({
      ...initialPose,
      error: gdm?.error || amcl?.error || "pose unavailable",
    });
  } catch (error) {
    if (gen !== generation || seq !== poseSeq) return;
    publish({
      ...poseState,
      live: false,
      error: error.message,
    });
  }
}

function ensurePolling() {
  if (poseTimer) return;
  loadStatus();
  loadPose();
  poseTimer = setInterval(loadPose, POSE_POLL_MS);
  statusTimer = setInterval(loadStatus, STATUS_POLL_MS);
}

function subscribe(listener) {
  listeners.add(listener);
  ensurePolling();
  return () => {
    listeners.delete(listener);
    if (listeners.size > 0) return;
    generation += 1;
    clearInterval(poseTimer);
    clearInterval(statusTimer);
    poseTimer = null;
    statusTimer = null;
  };
}

const disabledSnapshot = { pose: initialPose, status: initialStatus };

function subscribeDisabled() {
  return () => {};
}

function getDisabledSnapshot() {
  return disabledSnapshot;
}

export default function useGdmRobotPose(enabled = true) {
  return useSyncExternalStore(
    enabled ? subscribe : subscribeDisabled,
    enabled ? getSnapshot : getDisabledSnapshot,
    enabled ? getSnapshot : getDisabledSnapshot
  );
}

/** 위치 변화가 화면 리렌더를 일으키면 안 되는 곳에서 최신 포즈만 읽는다. */
export function useGdmRobotPoseRef() {
  const ref = useRef(getSnapshot().pose);
  useEffect(() => {
    ref.current = getSnapshot().pose;
    return subscribe(() => {
      ref.current = getSnapshot().pose;
    });
  }, []);
  return ref;
}

/** floorplan 캐시 키. 맵 이름이 같을 때는 리렌더하지 않는다. */
export function useGdmMapStamp() {
  return useSyncExternalStore(subscribe, getMapStamp, getMapStamp);
}
