const STORAGE_KEY = "air-scent-map-rotation";

/** name → { deg, epoch } while a save is still ahead of the robot. */
const pending = new Map();
const writtenAt = new Map();

export const MAP_ROTATION_EVENT = "air-scent-map-rotation";
export const MAP_ZOOM_EVENT = "air-scent-map-zoom";
export const MAP_ZOOM_MIN = 50;
export const MAP_ZOOM_MAX = 250;
export const MAP_ZOOM_DEFAULT = 100;
export const MAP_CENTER_EVENT = "air-scent-map-center";
export const MAP_CENTER_DEFAULT = 50;

/** Clockwise degrees in (-180, 180]. */
export function normalizeMapRotation(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return 0;
  let deg = Math.round(number * 10) / 10;
  deg = ((deg % 360) + 360) % 360;
  if (deg > 180) deg -= 360;
  if (Object.is(deg, -0)) deg = 0;
  return deg;
}

function readAll() {
  try {
    const parsed = JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}");
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

function readLocal(name) {
  if (!name) return null;
  const all = readAll();
  if (!Object.prototype.hasOwnProperty.call(all, name)) return null;
  return normalizeMapRotation(all[name]);
}

function writeLocal(name, deg, stamp) {
  const all = readAll();
  all[name] = deg;
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(all));
  } catch {
    // private mode / full storage
  }
  if (stamp) writtenAt.set(name, Date.now());
}

export function stageMapRotation(name, value) {
  const deg = normalizeMapRotation(value);
  const epoch = (pending.get(name)?.epoch ?? 0) + 1;
  pending.set(name, { deg, epoch });
  writeLocal(name, deg, true);
  window.dispatchEvent(
    new CustomEvent(MAP_ROTATION_EVENT, { detail: { name, degrees: deg } }),
  );
  return { degrees: deg, epoch };
}

export function commitMapRotation(name, value, epoch) {
  const current = pending.get(name);
  const deg = normalizeMapRotation(value);
  if (current && current.epoch === epoch && current.deg === deg) {
    pending.delete(name);
  }
}

const zoomKey = "air-scent-map-zoom";
const pendingZoom = new Map();
const zoomWrittenAt = new Map();

export function normalizeMapZoom(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return MAP_ZOOM_DEFAULT;
  return Math.min(MAP_ZOOM_MAX, Math.max(MAP_ZOOM_MIN, Math.round(number)));
}

function readZoomAll() {
  try {
    const parsed = JSON.parse(localStorage.getItem(zoomKey) || "{}");
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

function readLocalZoom(name) {
  if (!name) return null;
  const all = readZoomAll();
  if (!Object.prototype.hasOwnProperty.call(all, name)) return null;
  return normalizeMapZoom(all[name]);
}

function writeLocalZoom(name, zoom, stamp) {
  const all = readZoomAll();
  all[name] = zoom;
  try {
    localStorage.setItem(zoomKey, JSON.stringify(all));
  } catch {
    // private mode / full storage
  }
  if (stamp) zoomWrittenAt.set(name, Date.now());
}

export function stageMapZoom(name, value) {
  const zoom = normalizeMapZoom(value);
  const epoch = (pendingZoom.get(name)?.epoch ?? 0) + 1;
  pendingZoom.set(name, { zoom, epoch });
  writeLocalZoom(name, zoom, true);
  window.dispatchEvent(
    new CustomEvent(MAP_ZOOM_EVENT, { detail: { name, zoom } }),
  );
  return { zoom, epoch };
}

export function commitMapZoom(name, value, epoch) {
  const current = pendingZoom.get(name);
  const zoom = normalizeMapZoom(value);
  if (current && current.epoch === epoch && current.zoom === zoom) {
    pendingZoom.delete(name);
  }
}

export function displayedZoom(name, serverValue) {
  const pend = name ? pendingZoom.get(name) : null;
  if (pend) return pend.zoom;

  const recent = Boolean(name) && Date.now() - (zoomWrittenAt.get(name) || 0) < 10000;
  const local = readLocalZoom(name);
  if (recent && local != null) return local;

  const server = Number(serverValue);
  if (Number.isFinite(server)) {
    const zoom = normalizeMapZoom(server);
    if (name && local !== zoom) writeLocalZoom(name, zoom, false);
    return zoom;
  }
  return local ?? MAP_ZOOM_DEFAULT;
}

/** Pending edit wins, then a recent local edit, then the robot's saved angle. */
export function displayedRotation(name, serverValue) {
  const pend = name ? pending.get(name) : null;
  if (pend) return pend.deg;

  const recent = Boolean(name) && Date.now() - (writtenAt.get(name) || 0) < 10000;
  const local = readLocal(name);
  if (recent && local != null) return local;

  const server = Number(serverValue);
  if (Number.isFinite(server)) {
    const deg = normalizeMapRotation(server);
    if (name && local !== deg) writeLocal(name, deg, false);
    return deg;
  }
  return local ?? 0;
}

const centerKey = "air-scent-map-center";
const pendingCenter = new Map();
const centerWrittenAt = new Map();

export function normalizeMapCenter(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return MAP_CENTER_DEFAULT;
  const rounded = Math.round(number * 10) / 10;
  return Math.min(100, Math.max(0, rounded));
}

function readCenterAll() {
  try {
    const parsed = JSON.parse(localStorage.getItem(centerKey) || "{}");
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

function readLocalCenter(name) {
  if (!name) return null;
  const raw = readCenterAll()[name];
  if (!raw || typeof raw !== "object") return null;
  return {
    x: normalizeMapCenter(raw.x),
    y: normalizeMapCenter(raw.y),
  };
}

function writeLocalCenter(name, center, stamp) {
  const all = readCenterAll();
  all[name] = center;
  try {
    localStorage.setItem(centerKey, JSON.stringify(all));
  } catch {
    // private mode / full storage
  }
  if (stamp) centerWrittenAt.set(name, Date.now());
}

function sameCenter(a, b) {
  return a && b && a.x === b.x && a.y === b.y;
}

export function stageMapCenter(name, x, y) {
  const center = { x: normalizeMapCenter(x), y: normalizeMapCenter(y) };
  const epoch = (pendingCenter.get(name)?.epoch ?? 0) + 1;
  pendingCenter.set(name, { center, epoch });
  writeLocalCenter(name, center, true);
  window.dispatchEvent(
    new CustomEvent(MAP_CENTER_EVENT, { detail: { name, ...center } }),
  );
  return { ...center, epoch };
}

export function commitMapCenter(name, x, y, epoch) {
  const current = pendingCenter.get(name);
  const center = { x: normalizeMapCenter(x), y: normalizeMapCenter(y) };
  if (current && current.epoch === epoch && sameCenter(current.center, center)) {
    pendingCenter.delete(name);
  }
}

export function displayedCenter(name, serverX, serverY) {
  const pend = name ? pendingCenter.get(name) : null;
  if (pend) return pend.center;

  const recent = Boolean(name) && Date.now() - (centerWrittenAt.get(name) || 0) < 10000;
  const local = readLocalCenter(name);
  if (recent && local) return local;

  const server = {
    x: Number(serverX),
    y: Number(serverY),
  };
  if (Number.isFinite(server.x) && Number.isFinite(server.y)) {
    const center = {
      x: normalizeMapCenter(server.x),
      y: normalizeMapCenter(server.y),
    };
    if (name && !sameCenter(local, center)) writeLocalCenter(name, center, false);
    return center;
  }
  return local ?? { x: MAP_CENTER_DEFAULT, y: MAP_CENTER_DEFAULT };
}
