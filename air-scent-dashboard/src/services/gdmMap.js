import { wardDisplayName } from "../utils/wardStorage";

const API_BASE = import.meta.env.VITE_GDM_API ?? "";

export const GDM_FLOORPLAN_URL = `${API_BASE}/api/gdm/floorplan.png`;

export async function fetchGdmStatus() {
  const response = await fetch(`${API_BASE}/api/gdm/status`);
  if (!response.ok) {
    throw new Error(`gdm status failed (${response.status})`);
  }
  return response.json();
}

export async function fetchGdmRooms() {
  const response = await fetch(`${API_BASE}/api/gdm/rooms`);
  if (!response.ok) {
    throw new Error(`gdm rooms failed (${response.status})`);
  }
  return response.json();
}

export async function fetchGdmRobotPose() {
  const response = await fetch(`${API_BASE}/api/gdm/robot/pose`);
  if (!response.ok) {
    throw new Error(`gdm pose failed (${response.status})`);
  }
  return response.json();
}

/** polygon centroid → map-normalized percent (0–100) */
export function roomsToWards(roomsPayload) {
  const rooms = roomsPayload?.rooms ?? [];
  const [imgW, imgH] = roomsPayload?.image_size ?? [0, 0];

  if (!imgW || !imgH || rooms.length === 0) {
    return [];
  }

  return rooms.map((room, index) => {
    const poly = room.polygon_px ?? [];
    const cx =
      poly.reduce((sum, point) => sum + point[0], 0) / Math.max(poly.length, 1);
    const cy =
      poly.reduce((sum, point) => sum + point[1], 0) / Math.max(poly.length, 1);

    return {
      id: room.id || `gdm-room-${index + 1}`,
      name: wardDisplayName({
        id: room.id || `gdm-room-${index + 1}`,
        name: room.name || `방 ${index + 1}`,
      }),
      x: Number(((cx / imgW) * 100).toFixed(1)),
      y: Number(((cy / imgH) * 100).toFixed(1)),
      source: "gdm",
      sensorId: room.sensor_id ?? room.id ?? null,
      targetScent: room.target_scent ?? null,
      polygon: poly.map(([px, py]) => [
        Number(((px / imgW) * 100).toFixed(2)),
        Number(((py / imgH) * 100).toFixed(2)),
      ]),
    };
  });
}

export function computeContainLayout(
  containerW,
  containerH,
  naturalW,
  naturalH,
  rotationDeg = 0,
  zoomPercent = 100,
  centerX = 50,
  centerY = 50,
) {
  if (!containerW || !containerH || !naturalW || !naturalH) {
    return null;
  }

  const scale = Math.min(containerW / naturalW, containerH / naturalH);
  const width = naturalW * scale;
  const height = naturalH * scale;
  const rotation = Number.isFinite(Number(rotationDeg)) ? Number(rotationDeg) : 0;
  const rad = (rotation * Math.PI) / 180;
  const axis = Math.abs(Math.cos(rad));
  const cross = Math.abs(Math.sin(rad));
  const boundW = width * axis + height * cross;
  const boundH = width * cross + height * axis;
  const rotationScale =
    boundW > 0 && boundH > 0
      ? Math.min(1, containerW / boundW, containerH / boundH)
      : 1;
  const zoom = Number.isFinite(Number(zoomPercent)) ? Number(zoomPercent) : 100;
  const viewScale = rotationScale * (zoom / 100);
  const center = {
    x: clampCenter(centerX),
    y: clampCenter(centerY),
  };
  const pan = panForBox(width, height, rotation, viewScale, center.x, center.y);

  return {
    offsetX: (containerW - width) / 2,
    offsetY: (containerH - height) / 2,
    width,
    height,
    naturalW,
    naturalH,
    containerW,
    containerH,
    rotation,
    rotationScale,
    zoom,
    viewScale,
    centerX: center.x,
    centerY: center.y,
    panX: pan.x,
    panY: pan.y,
  };
}

function clampCenter(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return 50;
  return Math.min(100, Math.max(0, number));
}

function panForBox(width, height, rotationDeg, scale, centerX, centerY) {
  const cx = width / 2;
  const cy = height / 2;
  const scaled = scaleAround((centerX / 100) * width, (centerY / 100) * height, cx, cy, scale);
  const rotated = rotateAround(scaled.x, scaled.y, cx, cy, rotationDeg);
  return { x: cx - rotated.x, y: cy - rotated.y };
}

function rotateAround(x, y, cx, cy, deg) {
  const rad = (deg * Math.PI) / 180;
  const cos = Math.cos(rad);
  const sin = Math.sin(rad);
  const dx = x - cx;
  const dy = y - cy;
  return {
    x: cx + dx * cos - dy * sin,
    y: cy + dx * sin + dy * cos,
  };
}

function scaleAround(x, y, cx, cy, scale) {
  return {
    x: cx + (x - cx) * scale,
    y: cy + (y - cy) * scale,
  };
}

/**
 * Match CSS `transform: rotate(deg) scale(s)` (scale first, then clockwise rotate).
 * Inverse undoes that so pointer hits land on the unrotated map.
 */
function viewTransformPoint(xPx, yPx, layout, inverse) {
  const deg = Number(layout?.rotation) || 0;
  const scale = Number.isFinite(Number(layout?.viewScale))
    ? Number(layout.viewScale)
    : Number(layout?.rotationScale) || 1;
  const panX = Number(layout?.panX) || 0;
  const panY = Number(layout?.panY) || 0;
  if (!deg && scale === 1 && !panX && !panY) return { x: xPx, y: yPx };
  const cx = layout.containerW / 2;
  const cy = layout.containerH / 2;
  if (inverse) {
    const unrotated = rotateAround(xPx - panX, yPx - panY, cx, cy, -deg);
    return scaleAround(unrotated.x, unrotated.y, cx, cy, 1 / scale);
  }
  const scaled = scaleAround(xPx, yPx, cx, cy, scale);
  const rotated = rotateAround(scaled.x, scaled.y, cx, cy, deg);
  return { x: rotated.x + panX, y: rotated.y + panY };
}

/** Pixel shift that puts the chosen map percent on the image center. Scale is zoom/100 for the settings preview. */
export function viewPanPixels(width, height, rotationDeg, zoomPercent, centerX, centerY) {
  if (!width || !height) return { x: 0, y: 0 };
  return panForBox(
    width,
    height,
    Number(rotationDeg) || 0,
    (Number(zoomPercent) || 100) / 100,
    clampCenter(centerX),
    clampCenter(centerY),
  );
}

/** Screen drag of the picture → change in the map percent sitting on the crosshair. */
export function screenDragToCenterDelta(dx, dy, width, height, rotationDeg, zoomPercent) {
  const scale = (Number(zoomPercent) || 100) / 100 || 1;
  if (!width || !height) return { x: 0, y: 0 };
  const unrotated = rotateAround(-dx, -dy, 0, 0, -(Number(rotationDeg) || 0));
  return {
    x: (unrotated.x / scale / width) * 100,
    y: (unrotated.y / scale / height) * 100,
  };
}

/** map % → container % (contain letterbox, then the view rotation) */
export function mapPercentToContainer(xMap, yMap, layout) {
  if (!layout) {
    return { x: xMap, y: yMap };
  }

  const xPx = layout.offsetX + (xMap / 100) * layout.width;
  const yPx = layout.offsetY + (yMap / 100) * layout.height;
  const viewed = viewTransformPoint(xPx, yPx, layout, false);

  return {
    x: (viewed.x / layout.containerW) * 100,
    y: (viewed.y / layout.containerH) * 100,
  };
}

/**
 * container pointer → original map %.
 * View rotation, zoom, and center shift are removed here so stored pins and goals stay in the robot map frame.
 */
export function containerPointToMapPercent(clientX, clientY, rect, layout) {
  if (!layout) {
    const x = ((clientX - rect.left) / rect.width) * 100;
    const y = ((clientY - rect.top) / rect.height) * 100;
    return {
      x: Math.min(99.5, Math.max(0.5, x)),
      y: Math.min(99.5, Math.max(0.5, y)),
    };
  }

  const viewed = viewTransformPoint(
    clientX - rect.left,
    clientY - rect.top,
    layout,
    true,
  );
  const localX = viewed.x - layout.offsetX;
  const localY = viewed.y - layout.offsetY;

  if (localX < 0 || localY < 0 || localX > layout.width || localY > layout.height) {
    return null;
  }

  return {
    x: Math.min(99.5, Math.max(0.5, (localX / layout.width) * 100)),
    y: Math.min(99.5, Math.max(0.5, (localY / layout.height) * 100)),
  };
}

/** GDM pose x_px/y_px or amcl x_m/y_m → map % */
export function poseToMapPercent(pose, naturalW, naturalH, mapMeta = null) {
  const robotFrame = mapMeta?.source === "robot" && pose?.x_m != null && pose?.y_m != null;
  if (!robotFrame && pose?.x_px != null && pose?.y_px != null && naturalW && naturalH) {
    return {
      x: Math.min(98, Math.max(2, (pose.x_px / naturalW) * 100)),
      y: Math.min(98, Math.max(2, (pose.y_px / naturalH) * 100)),
      yaw: pose.yaw ?? 0,
      live: Boolean(pose.live),
      source: pose.source ?? "gdm",
    };
  }

  if (pose?.x_m != null && pose?.y_m != null && mapMeta) {
    const mapped = worldMetersToMapPercent(pose.x_m, pose.y_m, mapMeta);
    if (!mapped) return null;
    return {
      ...mapped,
      yaw: pose.yaw ?? 0,
      live: Boolean(pose.live),
      source: pose.source ?? "amcl",
    };
  }

  return null;
}

/**
 * map % → world meters in the driving Jetson's original map frame.
 * Percents must already be un-rotated (see containerPointToMapPercent).
 */
export function mapPercentToWorldMeters(xPercent, yPercent, mapMeta) {
  const [imgW, imgH] = mapMeta?.image_size ?? [];
  const resolution = mapMeta?.resolution;
  const origin = mapMeta?.origin;

  if (
    !imgW ||
    !imgH ||
    resolution == null ||
    !Array.isArray(origin) ||
    origin.length < 2
  ) {
    return null;
  }

  const px = (Number(xPercent) / 100) * imgW;
  const py = (Number(yPercent) / 100) * imgH;
  const x = Number(origin[0]) + px * Number(resolution);
  const y = Number(origin[1]) + (imgH - py) * Number(resolution);

  return { x, y, px, py };
}

/** Screen-down map % drag → ROS map yaw (CCW from +x). */
export function mapDragToYaw(from, to, minPercent = 1.2) {
  const dx = Number(to.x) - Number(from.x);
  const dy = Number(to.y) - Number(from.y);
  if (Math.hypot(dx, dy) < minPercent) return 0;
  return Math.atan2(-dy, dx);
}

/** ROS yaw (rad, CCW from +x) → CSS rotate deg (clockwise from +x). */
export function yawToCssDeg(yaw) {
  return (-Number(yaw || 0) * 180) / Math.PI;
}

/** Screen drag → ROS yaw in the unrotated map frame. */
export function screenDragToMapYaw(dx, dy, rotationDeg = 0) {
  const screenYaw = Math.atan2(-Number(dy) || 0, Number(dx) || 0);
  const view = ((Number(rotationDeg) || 0) * Math.PI) / 180;
  return normalizeYaw(screenYaw + view);
}

/** Robot heading drawn on a map that is itself rotated clockwise. */
export function displayYawCss(yaw, rotationDeg = 0) {
  return yawToCssDeg(yaw) + (Number(rotationDeg) || 0);
}

export function yawToDisplayDeg(yaw) {
  const deg = (Number(yaw || 0) * 180) / Math.PI;
  return Math.round((((deg % 360) + 360) % 360) * 10) / 10;
}

export function normalizeYaw(yaw) {
  let value = Number(yaw) || 0;
  while (value > Math.PI) value -= Math.PI * 2;
  while (value <= -Math.PI) value += Math.PI * 2;
  return value;
}

/** 현재 위치에서 목표를 바라보는 ROS yaw. 너무 가까우면 현재 방향 유지. */
export function yawTowardWorld(fromX, fromY, fromYaw, toX, toY) {
  const dx = Number(toX) - Number(fromX);
  const dy = Number(toY) - Number(fromY);
  if (!Number.isFinite(dx) || !Number.isFinite(dy) || Math.hypot(dx, dy) < 0.08) {
    return Number(fromYaw) || 0;
  }
  return Math.atan2(dy, dx);
}

export function yawFromRobotToMapPercent(
  pose,
  xPercent,
  yPercent,
  mapMeta,
  { reverse = false } = {},
) {
  const goal = mapPercentToWorldMeters(xPercent, yPercent, mapMeta);
  if (!goal || pose?.x_m == null || pose?.y_m == null) {
    return reverse ? Math.PI : 0;
  }

  let yaw = yawTowardWorld(pose.x_m, pose.y_m, pose.yaw, goal.x, goal.y);
  if (reverse) {
    yaw += Math.PI;
  }
  return normalizeYaw(yaw);
}

/** 주행 구역 [x, y, yaw, width, height](m) → 지도 % 네 꼭짓점. yaw 는 가로축 방향. */
export function orientedRectToMapPolygon(rect, mapMeta) {
  if (!Array.isArray(rect) || rect.length < 5) return null;
  const [cx, cy, yaw, width, height] = rect.map(Number);
  if (
    ![cx, cy, yaw, width, height].every(Number.isFinite) ||
    width <= 0 ||
    height <= 0
  ) {
    return null;
  }

  const cos = Math.cos(yaw);
  const sin = Math.sin(yaw);
  const halfW = width / 2;
  const halfH = height / 2;
  const corners = [
    [-halfW, -halfH],
    [halfW, -halfH],
    [halfW, halfH],
    [-halfW, halfH],
  ];
  const polygon = [];

  for (const [lx, ly] of corners) {
    const mapped = worldMetersToMapPercent(
      cx + lx * cos - ly * sin,
      cy + lx * sin + ly * cos,
      mapMeta,
      { clamp: false },
    );
    if (!mapped) return null;
    polygon.push([mapped.x, mapped.y]);
  }

  return polygon;
}

export async function fetchPatrolZones() {
  const response = await fetch(`${API_BASE}/api/nav/zones`);
  if (!response.ok) {
    throw new Error(`nav zones failed (${response.status})`);
  }
  const payload = await response.json().catch(() => ({}));
  return payload?.zones ?? null;
}

/** world meters → map % (inverse of mapPercentToWorldMeters) */
export function worldMetersToMapPercent(xM, yM, mapMeta, { clamp = true } = {}) {
  const [imgW, imgH] = mapMeta?.image_size ?? [];
  const resolution = mapMeta?.resolution;
  const origin = mapMeta?.origin;

  if (
    xM == null ||
    yM == null ||
    !imgW ||
    !imgH ||
    resolution == null ||
    !Array.isArray(origin) ||
    origin.length < 2 ||
    Number(resolution) === 0
  ) {
    return null;
  }

  const px = (Number(xM) - Number(origin[0])) / Number(resolution);
  const pyFromBottom = (Number(yM) - Number(origin[1])) / Number(resolution);
  const py = imgH - pyFromBottom;
  const x = (px / imgW) * 100;
  const y = (py / imgH) * 100;

  if (!clamp) {
    return { x, y };
  }

  return {
    x: Math.min(99.5, Math.max(0.5, x)),
    y: Math.min(99.5, Math.max(0.5, y)),
  };
}

export async function sendNavGoal({ x, y, yaw = 0, frame_id = "map" }) {
  const response = await fetch(`${API_BASE}/api/nav/goal`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ x, y, yaw, frame_id }),
  });

  const payload = await response.json().catch(() => ({}));
  if (!response.ok || !payload?.ok) {
    throw new Error(payload?.error || `nav goal failed (${response.status})`);
  }
  return payload;
}

export async function cancelNavGoal() {
  try {
    const response = await fetch(`${API_BASE}/api/nav/cancel`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    const payload = await response.json().catch(() => ({}));
    return { ok: true, ...payload };
  } catch {
    return { ok: true };
  }
}

/** AMCL 2D Pose Estimate → /initialpose */
async function postInitialPose(body) {
  const response = await fetch(`${API_BASE}/api/nav/initialpose`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const payload = await response.json().catch(() => ({}));
  return { response, payload };
}

export async function sendInitialPose({ x, y, yaw = 0, frame_id = "map" }) {
  const body = { x, y, yaw, frame_id };
  let { response, payload } = await postInitialPose(body);

  if ((!response.ok || payload?.ok === false) && response.status >= 500) {
    await new Promise((resolve) => setTimeout(resolve, 700));
    ({ response, payload } = await postInitialPose(body));
  }

  if (!response.ok || payload?.ok === false) {
    const detail = payload?.error || `HTTP ${response.status}`;
    throw new Error(`위치 추정을 보내지 못했습니다 (${detail})`);
  }
  return payload;
}

export async function fetchNavPose() {
  const response = await fetch(`${API_BASE}/api/nav/pose`);
  if (!response.ok) {
    throw new Error(`nav pose failed (${response.status})`);
  }
  return response.json();
}

export async function fetchNavStatus() {
  const response = await fetch(`${API_BASE}/api/nav/status`);
  if (!response.ok) {
    throw new Error(`nav status failed (${response.status})`);
  }
  return response.json();
}

/** std_msgs/Empty. 임무 노드가 받는 조작 명령. */
export async function publishImpactCommand(command) {
  const response = await fetch(`${API_BASE}/api/nav/command`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ command }),
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok || !payload?.ok) {
    throw new Error(payload?.error || `command failed (${response.status})`);
  }
  return payload;
}

export async function startGoalTeach() {
  const response = await fetch(`${API_BASE}/api/nav/teach/start`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: "{}",
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok || !payload?.ok) {
    throw new Error(payload?.error || `목표 설정 전환 실패 (${response.status})`);
  }
  return payload;
}

export async function saveTaughtZone(zone) {
  const response = await fetch(`${API_BASE}/api/nav/teach/zone`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(zone),
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok || !payload?.ok) {
    throw new Error(payload?.error || `구역 저장 실패 (${response.status})`);
  }
  return payload;
}

/** 분사 완료 뒤 정찰 재개. /go_around */
export async function publishGoAround() {
  const response = await fetch(`${API_BASE}/api/nav/go_around`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: "{}",
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok || !payload?.ok) {
    throw new Error(payload?.error || `go_around failed (${response.status})`);
  }
  return payload;
}

export async function publishDiffusionComplete() {
  return publishGoAround();
}
