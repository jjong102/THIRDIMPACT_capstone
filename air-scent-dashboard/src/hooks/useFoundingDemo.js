import { useCallback, useEffect, useRef, useState } from "react";

import { speakText } from "../services/ttsSpeak";

const STORAGE_KEY = "air-scent:founding-demo";
const TRIGGER_PERCENT = 20;
const PURIFY_MS = 10000;
const SPRAY_MS = 10000;
/** 공청 OFF 멘트가 끝난 뒤 발향 전까지 대기 (꺼지는 중인 공청이 향을 빨아들이지 않게) */
const PRE_SPRAY_WAIT_MS = 3000;
/**
 * 펌웨어는 M9xx 를 9초 켜고 1초 쉬는 주기로 반복한다. 같은 명령을 다시 받으면 릴레이는
 * 켠 채로 주기만 처음부터 세므로, 중간에 한 번 더 보내 SPRAY_MS 동안 끊기지 않게 한다.
 */
const SPRAY_REFRESH_MS = 5000;
/** 향 전환 시 전부 끄고 쉬는 시간 (릴레이 동시 전환 전류로 아두이노가 리셋되는 것 완화) */
const SWITCH_GAP_MS = 600;
/** 아두이노 재부팅(부트로더 + setup) 대기 후 재전송 */
const REBOOT_WAIT_MS = 2000;
const MAX_RETRIES = 3;
const PURIFY_DONE_TTS = "공기청정을 완료하였습니다.";
const DONE_TTS = "발향청정을 완료하였습니다.";

/**
 * main_opt 펌웨어: ON000/OFF000 = 2번 핀(공청 버튼, 상태 기억), M### = 미스트만.
 * 숫자 순서: 3번 우디(musk), 4번 플로럴(lavender), 5번 시트러스(woody)
 */
/** 핀 순서대로 3번 → 4번 → 5번 */
const SPRAY_STEPS = [
  { command: "M900", pin: 3, label: "Woody" },
  { command: "M090", pin: 4, label: "Floral" },
  { command: "M009", pin: 5, label: "Citrus" },
];

const NO_CHANNELS = { musk: false, lavender: false, woody: false };
const IDLE_STATE = {
  fragranceOn: false,
  fragranceDiffusing: false,
  fragranceChannels: NO_CHANNELS,
};

function loadArmed() {
  try {
    return window.localStorage.getItem(STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

function saveArmed(value) {
  try {
    window.localStorage.setItem(STORAGE_KEY, value ? "1" : "0");
  } catch {
    // ignore
  }
}

function scentPercent(mqttAirQuality) {
  const label = String(mqttAirQuality?.label ?? "").trim().toLowerCase();
  if (label !== "scent") return null;
  const value = mqttAirQuality?.confidence;
  return typeof value === "number" ? value : null;
}

function isAirReading(mqttAirQuality) {
  const label = String(mqttAirQuality?.label ?? "").trim();
  return /^(fresh(\s*air)?|air)$/i.test(label);
}

const wait = (ms) =>
  new Promise((resolve) => window.setTimeout(resolve, Math.max(0, ms)));

/** 펌웨어가 명령을 받으면 `입력값: [CMD]` 를 찍는다. 없으면 리셋으로 명령이 사라진 것. */
function commandAccepted(result, command) {
  const replies = Array.isArray(result?.replies) ? result.replies : [];
  return replies.some((line) => String(line).includes(`[${command}]`));
}

function looksRebooted(result) {
  const replies = Array.isArray(result?.replies) ? result.replies : [];
  return replies.some((line) => /READY|M133 \//.test(String(line)));
}

/**
 * 창설시연: scent ≥ 20% → 공청 10초 → 공청 OFF → 공청 완료 멘트 → 3초 대기
 * → 3번/4번/5번 핀 10초씩
 * → TTS → air 가 들어올 때까지 대기 → 다시 scent ≥ 20% 이면 반복
 */
export default function useFoundingDemo({
  mqttAirQuality,
  sendControlCommands,
  setExternalControl,
}) {
  const [armed, setArmed] = useState(loadArmed);
  const [phase, setPhase] = useState("idle");
  const [sprayLabel, setSprayLabel] = useState(null);
  const armedRef = useRef(armed);
  const runIdRef = useRef(0);
  const busyRef = useRef(false);
  const phaseRef = useRef("idle");
  const rearmedRef = useRef(true);
  const percent = scentPercent(mqttAirQuality);
  const isAir = isAirReading(mqttAirQuality);

  const setPhaseSafe = useCallback((next) => {
    phaseRef.current = next;
    setPhase(next);
  }, []);

  const send = useCallback(
    (commands, state = {}) =>
      Promise.resolve(
        sendControlCommands?.(commands, { ...IDLE_STATE, ...state })
      ).catch(() => null),
    [sendControlCommands]
  );

  /** 한 줄 명령을 보내고, 아두이노가 리셋돼 못 받았으면 부팅을 기다렸다가 다시 보낸다. */
  const sendChecked = useCallback(
    async (command, state, alive) => {
      for (let attempt = 0; attempt <= MAX_RETRIES; attempt += 1) {
        if (!alive()) return false;
        const result = await send([command], state);
        if (commandAccepted(result, command)) return true;
        console.warn(
          `[창설시연] ${command} 미수신${looksRebooted(result) ? " (아두이노 재부팅)" : ""} → 재전송 ${attempt + 1}/${MAX_RETRIES}`,
          result?.replies
        );
        await wait(REBOOT_WAIT_MS);
      }
      return false;
    },
    [send]
  );

  const runSequence = useCallback(async () => {
    const runId = runIdRef.current;
    const alive = () => runIdRef.current === runId;

    busyRef.current = true;
    rearmedRef.current = false;
    setExternalControl?.(true);
    try {
      setPhaseSafe("purifying");
      const purifyStart = Date.now();
      await sendChecked("ON000", { airPurifierOn: true }, alive);
      await wait(PURIFY_MS - (Date.now() - purifyStart));
      if (!alive()) return;

      await sendChecked("OFF000", { airPurifierOn: false }, alive);
      if (!alive()) return;

      await speakText(PURIFY_DONE_TTS);
      await wait(PRE_SPRAY_WAIT_MS);
      if (!alive()) return;

      setPhaseSafe("spraying");
      for (const [index, step] of SPRAY_STEPS.entries()) {
        if (!alive()) return;
        if (index > 0) {
          // 이전 향을 먼저 끄고 잠깐 쉰 뒤 다음 향을 켠다.
          setSprayLabel("전환 대기");
          await sendChecked("M000", { airPurifierOn: false }, alive);
          await wait(SWITCH_GAP_MS);
          if (!alive()) return;
        }
        setSprayLabel(`${step.pin}번 ${step.label}`);
        // 공유 상태에 fragranceOn/채널을 올리면 다른 화면이 ON00x 를 자동 전송해
        // 공청을 다시 켜고 미스트를 덮어쓰므로, 시퀀스 중에는 꺼짐 상태로 둔다.
        // 재전송으로 늦어져도 실제로 켜진 시점부터 SPRAY_MS 를 센다.
        await sendChecked(step.command, { airPurifierOn: false }, alive);
        const sprayStart = Date.now();
        await wait(SPRAY_REFRESH_MS);
        if (!alive()) return;
        await sendChecked(step.command, { airPurifierOn: false }, alive);
        await wait(SPRAY_MS - (Date.now() - sprayStart));
      }
      if (!alive()) return;

      await sendChecked("M000", { airPurifierOn: false }, alive);
      setSprayLabel(null);
      await speakText(DONE_TTS);
      if (alive()) setPhaseSafe("waiting");
    } finally {
      busyRef.current = false;
      if (alive()) setExternalControl?.(false);
    }
  }, [sendChecked, setExternalControl, setPhaseSafe]);

  useEffect(() => {
    if (!armed || busyRef.current) return;

    if (isAir) {
      rearmedRef.current = true;
      return;
    }
    if (percent != null && percent >= TRIGGER_PERCENT && rearmedRef.current) {
      void runSequence();
    }
  }, [armed, isAir, percent, runSequence]);

  const toggle = useCallback(() => {
    const next = !armedRef.current;
    const wasBusy = busyRef.current;
    const wasPurifying = phaseRef.current === "purifying";
    armedRef.current = next;
    saveArmed(next);
    runIdRef.current += 1;
    rearmedRef.current = true;
    setArmed(next);
    setSprayLabel(null);
    setPhaseSafe("idle");
    if (wasBusy) {
      void send(wasPurifying ? ["OFF000"] : ["M000"], {
        airPurifierOn: false,
      }).finally(() => setExternalControl?.(false));
    }
  }, [send, setExternalControl, setPhaseSafe]);

  return { armed, phase, sprayLabel, percent, toggle };
}
