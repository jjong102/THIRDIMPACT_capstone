import { speakText } from "../services/ttsSpeak";
import { wardDisplayName } from "./wardStorage";

export const TTS_PURIFY = "공기청정을 시작합니다.";
export const TTS_PURIFY_DONE_SPRAY =
  "공기청정을 완료했습니다. 향기분사를 시작합니다.";
export const TTS_SPRAY = TTS_PURIFY;
export const TTS_SPRAY_COMPLETE = "향기 분사가 완료되었습니다.";
export const TTS_COMPLETE = TTS_SPRAY_COMPLETE;
export const TTS_RETURN = "초기위치로 돌아갑니다.";
export const TTS_RESUME_PATROL = "정찰을 다시 시작합니다.";
export const TTS_COMPLETE_AND_RETURN = `${TTS_SPRAY_COMPLETE} ${TTS_RETURN}`;
export const TTS_ODOR_LIVING = "거실에서 악취를 감지했습니다.";

const STORAGE_PREFIX = "air-scent:tts:";

const lastSaid = {
  move: { text: "", at: 0 },
  purify: 0,
  purifyDone: 0,
  spray: 0,
  complete: 0,
  returnHome: 0,
};

function recently(bucket, text, windowMs) {
  const now = Date.now();
  if (typeof bucket === "number") {
    return now - bucket < windowMs;
  }
  return bucket.text === text && now - bucket.at < windowMs;
}

function markSaid(slot, text) {
  if (typeof lastSaid[slot] === "number") {
    lastSaid[slot] = Date.now();
    return;
  }
  lastSaid[slot] = { text, at: Date.now() };
}

function claimedStorage(slot, key) {
  const token = String(key || "").trim();
  if (!token) return false;
  try {
    const storeKey = `${STORAGE_PREFIX}${slot}`;
    if (window.sessionStorage.getItem(storeKey) === token) return true;
    window.sessionStorage.setItem(storeKey, token);
  } catch {
    // ignore
  }
  return false;
}

function sayOnce(slot, text, windowMs, storageKey = "") {
  if (claimedStorage(slot, storageKey)) return;
  if (recently(lastSaid[slot], text, windowMs)) return;
  markSaid(slot, text);
  void speakText(text);
}

/** 거실로 / 주방으로 */
export function destWithRo(name) {
  const value = String(name || "").trim();
  if (!value) return "지정한 위치로";
  const last = value.charCodeAt(value.length - 1);
  if (last < 0xac00 || last > 0xd7a3) return `${value}으로`;
  const jong = (last - 0xac00) % 28;
  if (jong === 0 || jong === 8) return `${value}로`;
  return `${value}으로`;
}

function resolveName(wardOrName) {
  if (wardOrName && typeof wardOrName === "object") {
    return wardDisplayName(wardOrName);
  }
  return String(wardOrName || "").trim();
}

export function resetMissionAnnouncements() {
  lastSaid.purify = 0;
  lastSaid.purifyDone = 0;
  lastSaid.spray = 0;
  lastSaid.complete = 0;
  lastSaid.returnHome = 0;
  try {
    Object.keys(window.sessionStorage)
      .filter((key) => key.startsWith(STORAGE_PREFIX) || key === "air-scent:spray-tts")
      .forEach((key) => window.sessionStorage.removeItem(key));
  } catch {
    // ignore
  }
}

export function announceMoveTo(wardOrName) {
  const name = resolveName(wardOrName);
  const text = `${destWithRo(name)} 이동합니다.`;
  if (recently(lastSaid.move, text, 4000)) return;
  lastSaid.move = { text, at: Date.now() };
  void speakText(text);
}

export function announceScentMission(wardOrName) {
  const name = resolveName(wardOrName);
  const text = `${destWithRo(name)} 이동해서 발향 공기청정을 합니다.`;
  if (recently(lastSaid.move, text, 4000)) return;
  lastSaid.move = { text, at: Date.now() };
  void speakText(text);
}

export function announcePurifyStart(arrivalKey = "") {
  sayOnce("purify", TTS_PURIFY, 20000, arrivalKey);
}

export function announcePurifyDoneAndSpray(arrivalKey = "") {
  sayOnce("purifyDone", TTS_PURIFY_DONE_SPRAY, 20000, arrivalKey);
}

export function announceSpray(arrivalKey = "") {
  announcePurifyStart(arrivalKey);
}

export function announceSprayComplete(arrivalKey = "") {
  sayOnce("complete", TTS_SPRAY_COMPLETE, 15000, arrivalKey);
}

export function announceComplete() {
  announceSprayComplete();
}

export function announceReturnHome(arrivalKey = "") {
  sayOnce("returnHome", TTS_RETURN, 15000, arrivalKey);
}

export function announceResumePatrol(arrivalKey = "") {
  sayOnce("returnHome", TTS_RESUME_PATROL, 15000, arrivalKey);
}

export function announceCompleteAndReturn(arrivalKey = "") {
  announceSprayComplete(arrivalKey);
  announceReturnHome(arrivalKey);
}
