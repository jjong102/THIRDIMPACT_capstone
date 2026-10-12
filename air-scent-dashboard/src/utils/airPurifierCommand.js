/** 펌웨어 MODE 번호(버튼 누른 순서) → 실제 공청기 모드: 1 고속 → 2 자동 → 3 수면 */
export const AIR_PURIFIER_MODE = {
  TURBO: 1,
  AUTO: 2,
  SLEEP: 3,
};

export const AIR_PURIFIER_MODE_KEYS = {
  [AIR_PURIFIER_MODE.TURBO]: "turbo",
  [AIR_PURIFIER_MODE.AUTO]: "auto",
  [AIR_PURIFIER_MODE.SLEEP]: "sleep",
};

export const AIR_PURIFIER_MODE_LABELS = {
  [AIR_PURIFIER_MODE.TURBO]: "고속모드",
  [AIR_PURIFIER_MODE.AUTO]: "자동모드",
  [AIR_PURIFIER_MODE.SLEEP]: "수면모드",
};

/** 펌웨어에 FANON/FANOFF 없음. 팬만 켤 때는 ON000, 끌 때는 OFF+현재미스트 */
export function buildAirOnCommand() {
  return "ON000";
}

export function buildAirOffCommand(mistDigits = "000") {
  const digits = String(mistDigits).padEnd(3, "0").slice(0, 3);
  return `OFF${digits}`;
}

export const AIR_PURIFIER_MODE_COMMAND = "MODE";

/** 공청기 릴레이는 건드리지 않고, 웹/펌웨어가 기억하는 현재 상태만 맞춘다. */
export function buildAirSyncCommand(on, mode = 1) {
  if (!on) {
    return "SYNC0";
  }

  const value = Number(mode);
  const next = value >= 1 && value <= 3 ? value : 1;
  return `SYNC${next}`;
}

export function getNextAirPurifierMode(mode) {
  return mode >= 3 ? 1 : mode + 1;
}

export function getModeStepsToTarget(current, target) {
  if (current === target) {
    return 0;
  }

  let mode = current;
  let steps = 0;

  while (mode !== target && steps < 3) {
    mode = getNextAirPurifierMode(mode);
    steps += 1;
  }

  return steps;
}
