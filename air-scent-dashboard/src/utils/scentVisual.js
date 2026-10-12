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
    emoji: "🦨",
    title: "악취 감지",
    short: "odor",
    color: "#6b5a1a",
    glow: "rgba(122, 92, 22, 0.58)",
    soft: "rgba(90, 78, 24, 0.42)",
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
    emoji: "🍃",
    title: "Fresh Air",
    short: "Fresh",
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
  if (/^(fresh(\s*air)?|air)$/i.test(text)) {
    return "Fresh Air";
  }
  const tone = sensorLabelTone(text);
  if (tone === "lavender") return "odor";
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
