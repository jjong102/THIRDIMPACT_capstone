import { scentLabel } from "../data/scentRecipes";
import { sensorLabelTone } from "./sensorLabels";

/** 냄새 분류 톤 → 이모지·색 (GDM/대시보드 톤) */

export const SCENT_TONES = {
  orange: {
    emoji: "🍊",
    title: "오렌지 감지",
    short: "Orange",
    color: "#d97706",
    glow: "rgba(217, 119, 6, 0.45)",
    soft: "rgba(251, 191, 36, 0.28)",
  },
  lavender: {
    emoji: "💜",
    title: "라벤더 감지",
    short: "Lavender",
    color: "#7c3aed",
    glow: "rgba(124, 58, 237, 0.42)",
    soft: "rgba(167, 139, 250, 0.3)",
  },
  /* 악취 (11/15) — 예전에는 라벤더 자리에 악취 모양이 있었다 */
  odor: {
    emoji: "🦨",
    title: "악취 감지",
    short: "악취",
    color: "#7c4a12",
    glow: "rgba(146, 64, 14, 0.55)",
    soft: "rgba(120, 72, 20, 0.4)",
  },
  mint: {
    emoji: "🌿",
    title: "페퍼민트 감지",
    short: "Peppermint",
    color: "#64748b",
    glow: "rgba(100, 116, 139, 0.4)",
    soft: "rgba(148, 163, 184, 0.3)",
  },
  fresh: {
    emoji: "",
    title: "무향",
    short: "무향",
    color: "#0d9488",
    glow: "rgba(20, 184, 166, 0.42)",
    soft: "rgba(45, 212, 191, 0.28)",
  },
  other: {
    emoji: "✨",
    title: "향 감지",
    short: "Scent",
    color: "#0284c7",
    glow: "rgba(14, 165, 233, 0.4)",
    soft: "rgba(56, 189, 248, 0.28)",
  },
  waiting: {
    emoji: "⏳",
    title: "측정 대기 중",
    short: "대기",
    color: "#94a3b8",
    glow: "rgba(148, 163, 184, 0.28)",
    soft: "rgba(203, 213, 225, 0.35)",
  },
  offline: {
    emoji: "📡",
    title: "연결 안 됨",
    short: "오프라인",
    color: "#94a3b8",
    glow: "rgba(148, 163, 184, 0.22)",
    soft: "rgba(226, 232, 240, 0.5)",
  },
  unknown: {
    emoji: "❔",
    title: "분석 중",
    short: "—",
    color: "#94a3b8",
    glow: "rgba(148, 163, 184, 0.25)",
    soft: "rgba(226, 232, 240, 0.4)",
  },
};

export const KITCHEN_DEMO_READING = {
  label: "Fresh Air",
  confidence: 96,
  tone: "fresh",
  isOrange: false,
  raw: "Fresh Air (96%)",
  roomId: "room_2",
};

export function isKitchenWard(ward) {
  if (ward == null) return false;
  const id = String(ward?.id ?? ward ?? "")
    .toLowerCase()
    .replace(/-/g, "_");
  const sensor = String(ward?.sensorId ?? ward?.sensor_id ?? "")
    .toLowerCase()
    .replace(/-/g, "_");
  const name = String(ward?.name ?? "").trim();
  return (
    id === "room_2" ||
    id === "room2" ||
    id === "ward_home_2" ||
    sensor === "room_2" ||
    sensor === "room2" ||
    name === "주방" ||
    /^room\s*2$/i.test(name)
  );
}

export function displayWardScentLabel(label) {
  const text = String(label ?? "").trim();
  if (!text) return null;
  if (/^(fresh(\s*air)?|air|무향)$/i.test(text)) {
    return "무향";
  }
  const tone = sensorLabelTone(text);
  if (tone === "lavender") return "Lavender";
  if (tone === "odor") return "odor";
  if (tone === "mint") return "Mint";
  if (tone === "orange") return "Orange";
  return text;
}

export function isOdorReading(reading) {
  const label = String(reading?.label ?? reading?.raw ?? "").trim();
  if (!label) return false;
  if (/^odor$/i.test(label)) return true;
  return displayWardScentLabel(label) === "odor";
}

export function resolveScentTone(reading) {
  if (!reading) return "waiting";

  const label = String(reading.label ?? reading.raw ?? "");
  const toneHint = reading.tone;

  // 라벨 우선 — air / Fresh Air 를 확실히 fresh로
  const fromLabel = sensorLabelTone(label);
  if (fromLabel) return fromLabel;

  if (toneHint && SCENT_TONES[toneHint] && toneHint !== "unknown") {
    return toneHint;
  }
  if (reading.isOrange || reading.is_orange) return "orange";
  if (label.trim()) return "other";
  return "waiting";
}

export function getScentVisual(reading, { connected = true } = {}) {
  const demoKitchen = reading === KITCHEN_DEMO_READING || reading?.roomId === "room_2";
  if (!connected && !demoKitchen && !reading?.label && !reading?.raw) {
    return { ...SCENT_TONES.offline, tone: "offline", label: null, detail: "브리지 미연결" };
  }

  const tone = resolveScentTone(reading);
  const base = SCENT_TONES[tone] ?? SCENT_TONES.unknown;
  const label = displayWardScentLabel(reading?.label) ?? reading?.label ?? null;
  const confidence =
    reading?.confidence != null ? `${reading.confidence}%` : null;

  // MQTT 원문 라벨을 제목으로 써서 Fresh Air(100%)가 바로 보이게
  const shown = label ? scentLabel(label) : null;
  const title = shown || base.title;
  const detail = [shown && shown !== title ? shown : null, confidence]
    .filter(Boolean)
    .join(" · ");

  return {
    ...base,
    tone,
    label,
    confidence,
    title,
    short: shown || base.short,
    detail: detail || base.title,
  };
}

/* ---------------------------------------------------------------- 새 와드 판정 (11/15)
 * wardState(diffuser/+/inference_ward) → 지도 효과. 우리 향은 그 향 색, 악취·모르는 냄새는 악취,
 * 깨끗함·약함·예열은 효과 없음. 효과 크기는 세기 %(0 = 공기 문턱, 100 = |Δ| 1.0)를 따른다. */
const WARD_SCENT_TONE = { Peppermint: "mint", Lavender: "lavender", Orange: "orange" };
const WARD_SPRAY_TONE = { peppermint: "mint", mint: "mint", lavender: "lavender", orange: "orange" };
const WARD_TONE_KO = { mint: "페퍼민트", lavender: "라벤더", orange: "오렌지", odor: "악취", other: "향" };
const WARD_NO_EFFECT = { ...SCENT_TONES.waiting, tone: "waiting", label: null, detail: "" };

export function wardJudgmentVisual(entry) {
  if (!entry) return null;
  const state = String(entry.state ?? "");
  let tone = null;
  if (state === "MALODOR" || state === "UNKNOWN") {
    tone = "odor";
  } else if (state === "SCENT") {
    // 지금 뿌리는 향(spraying)이 있으면 그게 더 정확하다 (센서 이름은 2~4° 차이라 추정)
    const spray = String(entry.spraying ?? "").toLowerCase();
    tone = WARD_SPRAY_TONE[spray] ?? WARD_SCENT_TONE[entry.label] ?? "other";
  }
  if (!tone) return WARD_NO_EFFECT;
  const pct = Math.max(0, Math.min(100, Number(entry.strength_pct) || 0));
  const intensity = pct / 100;
  const base = SCENT_TONES[tone] ?? SCENT_TONES.other;
  const name = state === "UNKNOWN" ? "모르는 냄새" : WARD_TONE_KO[tone];
  return {
    ...base,
    tone,
    label: name,
    title: name,
    short: `${name} ${pct}%`,
    detail: `세기 ${pct}%`,
    intensity,
    scale: 0.65 + 0.75 * intensity,            // 방 빛·오라 크기
    burstStep: pct >= 50 ? 1 : pct >= 20 ? 2 : 3, // 이모지 6개 중 몇 개마다 하나 (강할수록 많이)
  };
}

/** 와드 판정이 있으면 주방이 아닌 와드(지도에 1개)의 효과를 그것으로 바꾼다. */
export function applyWardJudgment(visuals, wards, entry) {
  const visual = wardJudgmentVisual(entry);
  if (!visual) return visuals;
  const next = { ...visuals };
  for (const ward of wards ?? []) {
    if (!isKitchenWard(ward)) next[ward.id] = visual;
  }
  return next;
}

/** ward ↔ MQTT room 매칭 */
export function readingForWard(rooms, ward, fallback = null) {
  if (!ward) return fallback;
  if (isKitchenWard(ward)) {
    return KITCHEN_DEMO_READING;
  }

  const map = rooms ?? {};
  const candidates = [
    ward.sensorId,
    ward.sensor_id,
    ward.id,
    ward.name,
  ].filter(Boolean);

  for (const key of candidates) {
    if (key && map[key] && !isKitchenRoomKey(key)) return map[key];
  }

  const roomKey = String(ward.id ?? "").replace(/^ward-/, "room_");
  if (map[roomKey] && !isKitchenRoomKey(roomKey)) return map[roomKey];

  // GDM room_1 ↔ default 센서 (거실용). 주방은 공유하지 않음.
  if (
    (ward.sensorId === "default" ||
      ward.sensor_id === "default" ||
      !ward.sensorId) &&
    map.default
  ) {
    return map.default;
  }

  return fallback;
}

function isKitchenRoomKey(key) {
  const id = String(key ?? "")
    .toLowerCase()
    .replace(/-/g, "_");
  return id === "room_2" || id === "room2";
}
