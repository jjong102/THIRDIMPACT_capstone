import { mockRobotData } from "../data/mockData";
import { clampTargetPercent } from "./fragranceIntensity";
import { blendForFragrance, scentDisplayName } from "../data/scentRecipes";

export const WARDS_STORAGE_KEY = "air-scent-wards";
export const WARD_SCENTS_STORAGE_KEY = "air-scent-ward-scents";
export const HOME_SCENT_WARDS_KEY = "air-scent-home-scent-wards";
export const SELECTED_WARD_ID_KEY = "air-scent-selected-ward";
export const MAX_HOME_SCENT_WARDS = 1;
export const MAX_WARDS = 1;

export const DEFAULT_WARDS = [
  { id: "ward-home-1", name: "안방", x: 28, y: 48 },
];

export const FRAGRANCE_PRESETS = [
  {
    id: "Mint",
    label: "Peppermint",
    blend: { mint: 100, lavender: 0, orange: 0 },
  },
  {
    id: "Lavender",
    label: "Lavender",
    blend: { mint: 0, lavender: 100, orange: 0 },
  },
  {
    id: "Orange",
    label: "Orange",
    blend: { mint: 0, lavender: 0, orange: 100 },
  },
];

export function defaultWardScent() {
  const fragrance = scentDisplayName(mockRobotData.currentFragrance);
  return {
    fragrance,
    level: clampTargetPercent(mockRobotData.fragranceLevel),
    blend: blendForFragrance(fragrance),
  };
}

const ROOM_DISPLAY_BY_ID = {
  room_1: "안방",
  room1: "안방",
  ward_home_1: "안방",
  room_2: "주방",
  room2: "주방",
  ward_home_2: "주방",
};

const ROOM_DISPLAY_BY_NAME = {
  room1: "안방",
  room_1: "안방",
  "302호": "안방",
  "거실": "안방",
  room2: "주방",
  room_2: "주방",
  "집2": "주방",
};

export function wardDisplayName(ward, fallbackName = "") {
  const id = String(ward?.id ?? ward ?? "").toLowerCase().replace(/-/g, "_");
  const name = String(ward?.name ?? fallbackName ?? "");
  if (ROOM_DISPLAY_BY_ID[id]) {
    return ROOM_DISPLAY_BY_ID[id];
  }
  const compact = name.toLowerCase().replace(/[\s_]/g, "");
  if (ROOM_DISPLAY_BY_NAME[compact] || ROOM_DISPLAY_BY_NAME[name]) {
    return ROOM_DISPLAY_BY_NAME[compact] || ROOM_DISPLAY_BY_NAME[name];
  }
  return name || String(ward?.id ?? ward ?? "") || "방";
}

export function loadWards() {
  try {
    const raw = localStorage.getItem(WARDS_STORAGE_KEY);
    if (!raw) return DEFAULT_WARDS.map((ward) => ({ ...ward }));
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed) || parsed.length === 0) {
      return DEFAULT_WARDS.map((ward) => ({ ...ward }));
    }
    return parsed.slice(0, MAX_WARDS).map((ward) => ({
      ...ward,
      name: wardDisplayName(ward),
    }));
  } catch {
    return DEFAULT_WARDS.map((ward) => ({ ...ward }));
  }
}

export function saveWards(wards) {
  localStorage.setItem(
    WARDS_STORAGE_KEY,
    JSON.stringify((wards ?? []).slice(0, MAX_WARDS))
  );
  try {
    window.dispatchEvent(new Event("air-scent-wards-changed"));
  } catch {
    // ignore
  }
}

export function loadWardScents() {
  try {
    const raw = localStorage.getItem(WARD_SCENTS_STORAGE_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

export function saveWardScents(scents) {
  localStorage.setItem(WARD_SCENTS_STORAGE_KEY, JSON.stringify(scents));
  try {
    window.dispatchEvent(new Event("air-scent-ward-scents-changed"));
  } catch {
    // ignore
  }
}

export function loadSelectedWardId(wards = loadWards()) {
  try {
    const id = localStorage.getItem(SELECTED_WARD_ID_KEY);
    if (id && wards.some((ward) => ward.id === id)) {
      return id;
    }
  } catch {
    // ignore
  }
  return wards[0]?.id ?? null;
}

export function saveSelectedWardId(id) {
  if (!id) {
    return null;
  }
  localStorage.setItem(SELECTED_WARD_ID_KEY, id);
  try {
    window.dispatchEvent(new Event("air-scent-selected-ward-changed"));
  } catch {
    // ignore
  }
  return id;
}

export function clearWardScent(wardId) {
  if (!wardId) {
    return loadWardScents();
  }
  const next = {
    ...loadWardScents(),
    [wardId]: { unset: true },
  };
  saveWardScents(next);
  return next;
}

export function persistWardScent(wardId, patch) {
  if (!wardId) {
    return loadWardScents();
  }

  const prev = loadWardScents();
  const current = getWardScent(prev, wardId);
  const fragrance = scentDisplayName(patch.fragrance ?? current.fragrance);
  const next = {
    ...prev,
    [wardId]: {
      ...current,
      ...patch,
      fragrance,
      blend: blendForFragrance(fragrance),
    },
  };
  saveWardScents(next);
  return next;
}

export function getWardScent(scents, wardId) {
  const saved = scents?.[wardId];
  if (!saved) return defaultWardScent();

  const fragrance = scentDisplayName(
    saved.fragrance ?? mockRobotData.currentFragrance
  );
  return {
    fragrance,
    level: clampTargetPercent(saved.level ?? mockRobotData.fragranceLevel),
    blend: blendForFragrance(fragrance),
  };
}

export function summarizeBlend(blend) {
  if (!blend) return "—";
  return `W${blend.mint} F${blend.lavender} C${blend.orange}`;
}

export function loadHomeScentWardIds() {
  try {
    const raw = localStorage.getItem(HOME_SCENT_WARDS_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(Boolean).slice(0, MAX_HOME_SCENT_WARDS);
  } catch {
    return [];
  }
}

export function saveHomeScentWardIds(ids) {
  const next = (ids ?? []).filter(Boolean).slice(0, MAX_HOME_SCENT_WARDS);
  localStorage.setItem(HOME_SCENT_WARDS_KEY, JSON.stringify(next));
  return next;
}
