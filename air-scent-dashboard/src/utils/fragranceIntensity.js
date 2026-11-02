import { scentIdFromText } from "../data/scentRecipes";
import { readingForWard, resolveScentTone } from "./scentVisual";
import { loadWards } from "./wardStorage";

/** 센서가 약함으로 보는 구간. 실제 최댓값은 98% 근처라 이를 100으로 둠 */
export const MQTT_PCT_FLOOR = 10;
export const MQTT_PCT_CEIL = 98;

export const LEVEL_TARGET_PCT = {
  1: MQTT_PCT_FLOOR,
  2: Math.round((MQTT_PCT_FLOOR + MQTT_PCT_CEIL) / 2),
  3: MQTT_PCT_CEIL,
};

export const LEVEL_MIST_SCALE = {
  1: 0.34,
  2: 0.67,
  3: 1,
};

/** 예전 1·2·3 단계와 10~98% 목표를 모두 받아 퍼센트로 맞춤 */
export function clampTargetPercent(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return LEVEL_TARGET_PCT[2];
  if (n === 1 || n === 2 || n === 3) return LEVEL_TARGET_PCT[n];
  return Math.min(MQTT_PCT_CEIL, Math.max(MQTT_PCT_FLOOR, Math.round(n)));
}

export function targetPercentForLevel(level) {
  return clampTargetPercent(level);
}

export function labelForTargetPercent(value) {
  const pct = clampTargetPercent(value);
  if (pct <= 28) return "약함";
  if (pct >= 80) return "강함";
  return "보통";
}

export function fragranceToTone(name, blend) {
  const scentId = scentIdFromText(name);
  if (scentId) return scentId.toLowerCase();

  const ranked = [
    ["mint", Number(blend?.mint) || 0],
    ["lavender", Number(blend?.lavender) || 0],
    ["orange", Number(blend?.orange) || 0],
  ].sort((a, b) => b[1] - a[1]);

  return ranked[0][1] > 0 ? ranked[0][0] : null;
}

export function matchingScentPercent(reading, expectedTone) {
  if (!reading) return null;

  const tone = resolveScentTone(reading);
  const confidence = Number(reading.confidence);
  if (!Number.isFinite(confidence)) return null;
  if (["fresh", "waiting", "unknown", "offline"].includes(tone)) {
    return 0;
  }
  if (expectedTone && ["orange", "lavender", "mint"].includes(expectedTone)) {
    return tone === expectedTone ? confidence : 0;
  }
  if (["orange", "lavender", "mint", "other"].includes(tone)) {
    return confidence;
  }
  return 0;
}

/** 10%→0, 98%→100 으로 슬라이더 트랙에 맞춤 */
export function liveTrackPercent(confidence) {
  if (!Number.isFinite(confidence)) return null;
  const span = MQTT_PCT_CEIL - MQTT_PCT_FLOOR;
  return Math.min(
    100,
    Math.max(0, ((confidence - MQTT_PCT_FLOOR) / span) * 100)
  );
}

export function quantizeMistScale(scale) {
  if (scale <= 0.04) return 0;
  return Math.max(1, Math.round(Math.min(1, scale) * 9)) / 9;
}

export function pickWardReading(mqttAirQuality, wardId) {
  const rooms = mqttAirQuality?.rooms ?? {};
  const fallback = mqttAirQuality?.label
    ? {
        label: mqttAirQuality.label,
        confidence: mqttAirQuality.confidence,
        tone: mqttAirQuality.tone,
        isOrange: mqttAirQuality.isOrange,
        receivedAt: mqttAirQuality.receivedAt,
      }
    : rooms.default ?? null;

  const wards = loadWards();
  const selected =
    (wardId && wards.find((ward) => String(ward.id) === String(wardId))) ||
    null;
  if (selected) {
    return readingForWard(rooms, selected, fallback);
  }

  let best = fallback;
  let bestPct = -1;
  for (const ward of wards) {
    const reading = readingForWard(rooms, ward, null);
    const pct = Number(reading?.confidence);
    if (Number.isFinite(pct) && pct > bestPct) {
      best = reading;
      bestPct = pct;
    }
  }
  return best;
}

export function resolveMistScale({
  fragranceLevel,
  currentFragrance,
  fragranceBlend,
  mqttAirQuality,
  wardId,
} = {}) {
  const target = clampTargetPercent(fragranceLevel);
  const base =
    (target - MQTT_PCT_FLOOR) / (MQTT_PCT_CEIL - MQTT_PCT_FLOOR);
  const expectedTone = fragranceToTone(currentFragrance, fragranceBlend);
  const reading = pickWardReading(mqttAirQuality, wardId);
  const current = matchingScentPercent(reading, expectedTone);

  if (current == null) {
    return quantizeMistScale(Math.max(0.22, base));
  }

  const error = target - current;
  if (error <= 0) return 0;

  const span = Math.max(6, MQTT_PCT_CEIL - current);
  const progress = Math.min(1, error / span);
  return quantizeMistScale(Math.max(0.12, 0.2 + 0.8 * progress));
}

export function applyMistScale(digits, scale) {
  if (!digits || scale <= 0) return "000";
  const factor = Math.min(1, Number(scale) || 1);
  if (factor >= 0.99) return String(digits).slice(0, 3).padEnd(3, "0");

  return String(digits)
    .slice(0, 3)
    .padEnd(3, "0")
    .split("")
    .map((ch) => {
      const n = Number(ch);
      if (!Number.isFinite(n) || n <= 0) return "0";
      return String(Math.min(9, Math.max(1, Math.round(n * factor))));
    })
    .join("");
}
