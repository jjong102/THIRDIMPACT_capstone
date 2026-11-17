import { useEffect, useState } from "react";

/**
 * 지도 와드 효과 미리보기 (11/15) — 실제 냄새 없이 와드 주변 효과를 켜 보는 시연·확인용.
 * 이 화면(브라우저)에만 있고 서버·판정·출동에는 아무 영향이 없다.
 * tone: "mint" | "lavender" | "orange" | "odor" | null(끔)
 */
const EVENT = "air-scent-ward-preview-changed";
let current = null;

export const WARD_PREVIEW_OPTIONS = [
  { tone: "mint", emoji: "🌿", label: "민트" },
  { tone: "lavender", emoji: "💜", label: "라벤더" },
  { tone: "orange", emoji: "🍊", label: "오렌지" },
  { tone: "odor", emoji: "🦨", label: "악취" },
];

const SPRAY_NAME = { mint: "peppermint", lavender: "lavender", orange: "orange" };

export function getWardPreview() {
  return current;
}

export function setWardPreview(tone) {
  current = tone || null;
  window.dispatchEvent(new CustomEvent(EVENT, { detail: current }));
}

export function toggleWardPreview(tone) {
  setWardPreview(current === tone ? null : tone);
}

/** 미리보기 → 와드 판정(wardState)과 같은 모양 (세기 60% 고정) */
export function previewEntry(tone) {
  if (!tone) return null;
  const now = Date.now() / 1000;
  if (tone === "odor") {
    return { state: "MALODOR", label: "Vinegar", strength_pct: 60, rx_time: now, preview: true };
  }
  return { state: "SCENT", spraying: SPRAY_NAME[tone], strength_pct: 60, rx_time: now, preview: true };
}

export function useWardPreview() {
  const [tone, setTone] = useState(current);
  useEffect(() => {
    const sync = (event) => setTone(event.detail ?? null);
    window.addEventListener(EVENT, sync);
    return () => window.removeEventListener(EVENT, sync);
  }, []);
  return tone;
}
