import { mockRobotData } from "../data/mockData";
import {
  blendForFragrance,
  channelsForFragrance,
  scentDisplayName,
} from "../data/scentRecipes";
import { clampTargetPercent } from "./fragranceIntensity";
import {
  defaultWardScent,
  getWardScent,
  loadSelectedWardId,
  loadWardScents,
} from "./wardStorage";

const STORAGE_KEY = "air-scent-fragrance-session";

export function loadFragranceSession() {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) {
      return null;
    }
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === "object" ? parsed : null;
  } catch {
    return null;
  }
}

export function saveFragranceSession(patch) {
  const prev = loadFragranceSession() ?? {};
  const next = { ...prev };

  for (const [key, value] of Object.entries(patch ?? {})) {
    if (value !== undefined) {
      next[key] = value;
    }
  }

  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
  } catch {
    // ignore storage errors
  }
  return next;
}

export function loadInitialFragranceState() {
  const session = loadFragranceSession() ?? {};
  const selectedWardId = loadSelectedWardId();
  const wardScent = selectedWardId
    ? getWardScent(loadWardScents(), selectedWardId)
    : defaultWardScent();

  const fragrance = scentDisplayName(
    session.currentFragrance || wardScent.fragrance || mockRobotData.currentFragrance
  );
  const recommended = scentDisplayName(
    session.recommendedFragrance || fragrance
  );
  const blend = blendForFragrance(fragrance);
  const level = clampTargetPercent(
    session.fragranceLevel ?? wardScent.level ?? mockRobotData.fragranceLevel
  );

  return {
    currentFragrance: fragrance,
    recommendedFragrance: recommended,
    recommendedReason: session.recommendedReason ?? null,
    fragranceLevel: level,
    fragranceBlend: blend,
    fragranceChannels: session.fragranceOn
      ? channelsForFragrance(fragrance)
      : { mint: false, lavender: false, orange: false },
    fragranceOn: Boolean(session.fragranceOn),
    fragranceDiffusing: Boolean(session.fragranceDiffusing),
    fragranceDispenseComplete: Boolean(session.fragranceDispenseComplete),
    lastDispenseAt: session.lastDispenseAt ?? null,
  };
}
