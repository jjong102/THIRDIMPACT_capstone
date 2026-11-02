import { sensorLabelTone } from "./sensorLabels";

const PAYLOAD_RE = /^(.+?)\s*\(\s*(\d+(?:\.\d+)?)\s*%\s*\)\s*$/;

export function parseAirQualityPayload(payload) {
  if (!payload || typeof payload !== "string") {
    return {
      raw: null,
      label: null,
      confidence: null,
      isOrange: false,
      tone: "unknown",
    };
  }

  const text = payload.trim();
  const match = PAYLOAD_RE.exec(text);

  let label = text;
  let confidence = null;

  if (match) {
    label = match[1].trim();
    const parsed = Number(match[2]);
    confidence = Number.isNaN(parsed) ? null : parsed;
  }

  const tone = sensorLabelTone(label) ?? "other";

  return {
    raw: text,
    label,
    confidence,
    isOrange: tone === "orange",
    tone,
  };
}
