/** 채널 순서 = 펌웨어 D3(페퍼민트 MNT), D4(라벤더 LVD), D5(오렌지 ORG) */
export const MIST_CHANNELS = ["mint", "lavender", "orange"];

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

/** 펌웨어 채널 이름 (air_control 2.1): MIST MNT+LVD / MIST ALL / MIST OFF */
export const MIST_NAMES = { mint: "MNT", lavender: "LVD", orange: "ORG" };
export const MIST_OFF_COMMAND = "MIST OFF";

/**
 * 켜진 채널 → 분무 명령 한 줄. 켜진 채널은 끊김 없이 계속 분무한다 (세기는 항상 최대).
 * mistScale 은 0 이하(목표 도달)일 때 끄는 데만 쓴다.
 */
export function buildMistCommand({ fragranceOn, fragranceChannels, mistScale = 1 }) {
  if (!fragranceOn || mistScale <= 0) return MIST_OFF_COMMAND;
  const active = MIST_CHANNELS.filter((id) => fragranceChannels?.[id] === true);
  if (active.length === 0) return MIST_OFF_COMMAND;
  if (active.length === MIST_CHANNELS.length) return "MIST ALL";
  return `MIST ${active.map((id) => MIST_NAMES[id]).join("+")}`;
}

export function buildFragranceCommand(args) {
  return { command: buildMistCommand(args) };
}

export function weightToSeconds(weight) {
  return Number(weight) || 0;
}
