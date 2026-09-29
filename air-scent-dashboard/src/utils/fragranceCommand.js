import { applyMistScale } from "./fragranceIntensity";

/** MIST1 D3 우디(musk), MIST2 D4 플로럴(lavender), MIST3 D5 시트러스(woody) */
export const MIST_CHANNELS = ["musk", "lavender", "woody"];

/** 펌웨어: 가중치 1 = 1000ms */
export const LEVEL_UNIT_MS = {
  1: 1000,
  2: 1000,
  3: 1000,
};

const TOTAL_WEIGHT = 9;

function distributeWeights(values) {
  const total = values.reduce((sum, value) => sum + value, 0);
  if (total === 0) {
    return [0, 0, 0];
  }

  const raw = values.map((value) => (value / total) * TOTAL_WEIGHT);
  const weights = raw.map((value) => Math.floor(value));
  let remainder = TOTAL_WEIGHT - weights.reduce((sum, value) => sum + value, 0);

  const order = raw
    .map((value, index) => ({ index, frac: value - Math.floor(value) }))
    .sort((a, b) => b.frac - a.frac);

  for (let i = 0; i < remainder; i++) {
    weights[order[i % order.length].index]++;
  }

  return weights;
}

/** 블렌드 비율 → 0~9 가중치 3자리 (합 9) */
export function blendToWeights(blend) {
  const values = MIST_CHANNELS.map((id) => blend[id] ?? 0);
  const weights = distributeWeights(values);
  return weights.map((weight) => String(weight)).join("");
}

export function buildMistDigits({
  fragranceOn,
  fragranceChannels,
  mistScale = 1,
}) {
  if (!fragranceOn) {
    return "000";
  }

  const active = MIST_CHANNELS.map((id) => fragranceChannels?.[id] === true);
  if (!active.some(Boolean)) {
    return "000";
  }

  // 켜진 채널은 모두 같은 시간 분사한다. 블렌드 비율로 나누면 수동으로 켠 채널이
  // 남은 레시피 비율(예: Woody 100%) 때문에 1초만 켜졌다 꺼진다.
  return applyMistScale(
    active.map((isOn) => (isOn ? "9" : "0")).join(""),
    mistScale
  );
}

/** air_control.ino: M133 미스트만 켜기, M000 미스트만 끄기 (팬은 그대로) */
export function buildFragranceCommand({
  fragranceOn,
  fragranceBlend,
  fragranceChannels,
  mistScale = 1,
}) {
  const mist = buildMistDigits({
    fragranceOn,
    fragranceBlend,
    fragranceChannels,
    mistScale,
  });

  return {
    mist,
    command: `M${mist}`,
  };
}

export function weightToSeconds(weight) {
  return Number(weight) || 0;
}
