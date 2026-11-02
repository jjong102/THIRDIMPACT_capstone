import { useCallback, useEffect, useRef, useState } from "react";

import {
  fetchDeviceState,
  fetchFragranceHealth,
  putDeviceState,
  reconnectFragrance,
  sendFragranceCommands,
} from "../services/fragranceSerial";
import {
  buildAirOnCommand,
  buildAirOffCommand,
  buildAirSyncCommand,
  getModeStepsToTarget,
  AIR_PURIFIER_MODE_COMMAND,
} from "../utils/airPurifierCommand";
import { buildFragranceCommand, buildMistDigits } from "../utils/fragranceCommand";
import { clampTargetPercent } from "../utils/fragranceIntensity";

const HEALTH_POLL_MS = 10000;
const HEALTH_RETRY_MS = 3000;
const STATE_POLL_MS = 1000;

/** 블렌드 슬라이더 드래그 중에만 사용. 전원/채널/강도는 즉시 전송. */
const BLEND_SEND_DELAY_MS = 400;

function sameChannels(a, b) {
  if (a === b) {
    return true;
  }

  const keys = Object.keys(a ?? {});
  const other = Object.keys(b ?? {});
  if (keys.length !== other.length) {
    return false;
  }

  return keys.every((key) => a[key] === b[key]);
}

function sameBlend(a, b) {
  if (a === b) {
    return true;
  }

  return (
    a?.mint === b?.mint &&
    a?.lavender === b?.lavender &&
    a?.orange === b?.orange
  );
}

function normalizeSharedState(raw) {
  if (!raw || typeof raw !== "object") {
    return null;
  }

  const mode = Number(raw.airPurifierMode);
  const level = Number(raw.fragranceLevel);

  return {
    rev: Number(raw.rev) || 0,
    airPurifierOn: Boolean(raw.airPurifierOn),
    airPurifierMode: mode >= 1 && mode <= 3 ? mode : 1,
    fragranceOn: Boolean(raw.fragranceOn),
    fragranceChannels: {
      mint: Boolean(raw.fragranceChannels?.mint),
      lavender: Boolean(raw.fragranceChannels?.lavender),
      orange: Boolean(raw.fragranceChannels?.orange),
    },
    fragranceLevel: clampTargetPercent(level),
    fragranceBlend: {
      mint: Number(raw.fragranceBlend?.mint) || 0,
      lavender: Number(raw.fragranceBlend?.lavender) || 0,
      orange: Number(raw.fragranceBlend?.orange) || 0,
    },
    fragranceDiffusing: Boolean(raw.fragranceDiffusing),
  };
}

function applyFanStatusFromReplies(replies, setAirPurifierOn, setAirPurifierMode) {
  for (const line of replies) {
    const text = String(line);

    if (text.includes("FAN OFF") && !text.includes("이미")) {
      setAirPurifierOn(false);
      continue;
    }

    const modeMatch = text.match(/FAN(?: ON)? \/ MODE\s*(\d)|FAN MODE\s*(\d)/);
    if (modeMatch) {
      const mode = Number(modeMatch[1] || modeMatch[2]);
      if (mode >= 1 && mode <= 3) {
        setAirPurifierOn(true);
        setAirPurifierMode(mode);
      }
      continue;
    }

    if (text.includes("FAN 이미 ON")) {
      setAirPurifierOn(true);
    }
  }
}

export default function useArduinoControllers({
  airPurifierOn,
  setAirPurifierOn,
  airPurifierMode,
  setAirPurifierMode,
  fragranceOn,
  setFragranceOn,
  fragranceChannels,
  setFragranceChannels,
  fragranceLevel,
  setFragranceLevel,
  fragranceBlend,
  setFragranceBlend,
  fragranceDiffusing,
  setFragranceDiffusing,
  setFragranceDispenseComplete,
  mistScale = 1,
}) {
  const lastFragranceSentRef = useRef("");
  const airStatusRef = useRef({
    on: airPurifierOn,
    mode: airPurifierMode,
  });
  airStatusRef.current = {
    on: airPurifierOn,
    mode: airPurifierMode,
  };
  const syncedOnConnectRef = useRef(false);
  const lastRevRef = useRef(-1);
  const applyingRemoteRef = useRef(false);
  const hydratedRef = useRef(false);
  const mistScaleRef = useRef(mistScale);
  mistScaleRef.current = mistScale;
  const prevFragranceRef = useRef({
    fragranceOn,
    fragranceChannels,
    fragranceLevel,
    fragranceBlend,
    mistScale,
  });
  const commandQueueRef = useRef(Promise.resolve());
  /** 창설시연 등 시퀀스가 직접 제어하는 동안 자동 전송(ON000 등)을 막는다. */
  const externalControlRef = useRef(false);
  const [arduinoConnected, setArduinoConnected] = useState(false);
  const [hydrated, setHydrated] = useState(false);
  const [isAirSending, setIsAirSending] = useState(false);
  const [isFragranceSending, setIsFragranceSending] = useState(false);

  const fragranceStateRef = useRef({
    fragranceOn,
    fragranceChannels,
    fragranceLevel,
    fragranceBlend,
    fragranceDiffusing,
  });
  fragranceStateRef.current = {
    fragranceOn,
    fragranceChannels,
    fragranceLevel,
    fragranceBlend,
    fragranceDiffusing,
  };

  const snapshotState = useCallback(
    (overrides = {}) => ({
      airPurifierOn: airStatusRef.current.on,
      airPurifierMode: airStatusRef.current.mode,
      fragranceOn: fragranceStateRef.current.fragranceOn,
      fragranceChannels: { ...fragranceStateRef.current.fragranceChannels },
      fragranceLevel: fragranceStateRef.current.fragranceLevel,
      fragranceBlend: { ...fragranceStateRef.current.fragranceBlend },
      fragranceDiffusing: Boolean(fragranceStateRef.current.fragranceDiffusing),
      ...overrides,
    }),
    [],
  );

  const rememberRev = useCallback((state) => {
    const rev = Number(state?.rev) || 0;
    if (rev > lastRevRef.current) {
      lastRevRef.current = rev;
    }
  }, []);

  const applySharedState = useCallback(
    (raw) => {
      const next = normalizeSharedState(raw);
      if (!next || next.rev <= 0 || next.rev <= lastRevRef.current) {
        return false;
      }

      lastRevRef.current = next.rev;
      applyingRemoteRef.current = true;
      airStatusRef.current = {
        on: next.airPurifierOn,
        mode: next.airPurifierMode,
      };
      fragranceStateRef.current = {
        fragranceOn: next.fragranceOn,
        fragranceChannels: next.fragranceChannels,
        fragranceLevel: next.fragranceLevel,
        fragranceBlend: next.fragranceBlend,
        fragranceDiffusing: next.fragranceDiffusing,
      };
      prevFragranceRef.current = {
        fragranceOn: next.fragranceOn,
        fragranceChannels: next.fragranceChannels,
        fragranceLevel: next.fragranceLevel,
        fragranceBlend: next.fragranceBlend,
      };
      lastFragranceSentRef.current = buildFragranceCommand({
        fragranceOn: next.fragranceOn,
        fragranceBlend: next.fragranceBlend,
        fragranceChannels: next.fragranceChannels,
        mistScale: mistScaleRef.current,
        airPurifierOn: next.airPurifierOn,
      }).command;

      setAirPurifierOn(next.airPurifierOn);
      setAirPurifierMode(next.airPurifierMode);
      setFragranceOn(next.fragranceOn);
      setFragranceChannels(next.fragranceChannels);
      setFragranceLevel?.(next.fragranceLevel);
      setFragranceBlend?.(next.fragranceBlend);
      setFragranceDiffusing?.(next.fragranceDiffusing);

      return true;
    },
    [
      setAirPurifierOn,
      setAirPurifierMode,
      setFragranceOn,
      setFragranceChannels,
      setFragranceLevel,
      setFragranceBlend,
      setFragranceDiffusing,
    ],
  );

  const ingestSharedState = useCallback(
    async (raw) => {
      const next = normalizeSharedState(raw);
      if (!next) {
        return;
      }

      if (!hydratedRef.current && next.rev === 0) {
        hydratedRef.current = true;
        setHydrated(true);
        try {
          const result = await putDeviceState(snapshotState());
          rememberRev(result.state);
        } catch {
          lastRevRef.current = 0;
        }
        return;
      }

      if (applySharedState(next)) {
        hydratedRef.current = true;
        setHydrated(true);
        return;
      }

      if (!hydratedRef.current) {
        hydratedRef.current = true;
        setHydrated(true);
      }
    },
    [applySharedState, rememberRev, snapshotState],
  );

  const enqueueCommands = useCallback((commands, channel, stateOverrides) => {
    const lines = (commands ?? []).filter(Boolean);
    if (lines.length === 0) {
      return commandQueueRef.current;
    }

    const run = async () => {
      if (channel === "air") {
        setIsAirSending(true);
      } else {
        setIsFragranceSending(true);
      }

      try {
        const result = await sendFragranceCommands(
          lines,
          snapshotState(stateOverrides),
        );
        setArduinoConnected(Boolean(result.connected));
        if (result.connected) {
          lastFragranceSentRef.current = String(lines[lines.length - 1] ?? "");
        } else {
          lastFragranceSentRef.current = "";
        }
        applyFanStatusFromReplies(
          result.replies ?? [],
          setAirPurifierOn,
          setAirPurifierMode,
        );
        rememberRev(result.state);
        return true;
      } catch (error) {
        lastFragranceSentRef.current = "";
        setArduinoConnected(false);
        console.warn(`[${channel}] send failed`, error);
        return false;
      } finally {
        if (channel === "air") {
          setIsAirSending(false);
        } else {
          setIsFragranceSending(false);
        }
      }
    };

    commandQueueRef.current = commandQueueRef.current
      .catch(() => {})
      .then(run);

    return commandQueueRef.current;
  }, [rememberRev, setAirPurifierOn, setAirPurifierMode, snapshotState]);

  useEffect(() => {
    if (applyingRemoteRef.current) {
      applyingRemoteRef.current = false;
      return undefined;
    }

    const { command } = buildFragranceCommand({
      fragranceOn,
      fragranceBlend,
      fragranceChannels,
      mistScale,
      airPurifierOn: airStatusRef.current.on,
    });
    const prev = prevFragranceRef.current;
    prevFragranceRef.current = {
      fragranceOn,
      fragranceChannels,
      fragranceLevel,
      fragranceBlend,
      mistScale,
    };

    if (
      externalControlRef.current ||
      !command ||
      command === lastFragranceSentRef.current
    ) {
      return undefined;
    }

    const blendOnly =
      prev.fragranceOn === fragranceOn &&
      prev.fragranceLevel === fragranceLevel &&
      prev.mistScale === mistScale &&
      sameChannels(prev.fragranceChannels, fragranceChannels) &&
      !sameBlend(prev.fragranceBlend, fragranceBlend);
    const delay = blendOnly ? BLEND_SEND_DELAY_MS : 0;

    const timer = setTimeout(() => {
      lastFragranceSentRef.current = command;
      enqueueCommands([command], "fragrance");
    }, delay);

    return () => clearTimeout(timer);
  }, [
    fragranceOn,
    fragranceChannels,
    fragranceLevel,
    fragranceBlend,
    mistScale,
    enqueueCommands,
  ]);

  useEffect(() => {
    let cancelled = false;

    async function pollHealth() {
      try {
        const health = await fetchFragranceHealth();
        if (!cancelled) {
          setArduinoConnected(Boolean(health.connected));
          if (health.state) {
            await ingestSharedState(health.state);
          }
        }
      } catch {
        if (!cancelled) {
          setArduinoConnected(false);
        }
      }
    }

    pollHealth();
    const timer = setInterval(
      pollHealth,
      arduinoConnected ? HEALTH_POLL_MS : HEALTH_RETRY_MS
    );

    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [arduinoConnected, ingestSharedState]);

  const reconnectArduino = useCallback(async () => {
    try {
      const result = await reconnectFragrance();
      setArduinoConnected(Boolean(result.connected));
      if (result.state) {
        await ingestSharedState(result.state);
      }
      return Boolean(result.connected);
    } catch {
      try {
        const health = await fetchFragranceHealth();
        setArduinoConnected(Boolean(health.connected));
        if (health.state) {
          await ingestSharedState(health.state);
        }
        return Boolean(health.connected);
      } catch {
        setArduinoConnected(false);
        return false;
      }
    }
  }, [ingestSharedState]);

  useEffect(() => {
    let cancelled = false;

    async function pollState() {
      try {
        const result = await fetchDeviceState();
        if (cancelled) {
          return;
        }
        if (result.state) {
          await ingestSharedState(result.state);
        }
      } catch {
        // keep last known local state
      }
    }

    pollState();
    const timer = setInterval(pollState, STATE_POLL_MS);

    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [ingestSharedState]);

  useEffect(() => {
    if (!arduinoConnected) {
      syncedOnConnectRef.current = false;
      return;
    }

    if (!hydrated || syncedOnConnectRef.current || externalControlRef.current) {
      return;
    }

    syncedOnConnectRef.current = true;
    const { on } = airStatusRef.current;
    const { fragranceOn: mistOn, fragranceChannels: channels, fragranceBlend: blend } =
      fragranceStateRef.current;
    const mistDigits = buildMistDigits({
      fragranceOn: mistOn,
      fragranceChannels: channels,
      fragranceBlend: blend,
      mistScale: mistScaleRef.current,
    });
    const commands = [
      on
        ? mistDigits === "000"
          ? buildAirOnCommand()
          : `ON${mistDigits}`
        : buildAirOffCommand(mistDigits),
    ];
    enqueueCommands(commands, "air");
  }, [arduinoConnected, hydrated, enqueueCommands]);

  const toggleAirPurifier = useCallback(() => {
    const next = !airStatusRef.current.on;
    const { fragranceOn: mistOn, fragranceChannels: channels, fragranceBlend: blend } =
      fragranceStateRef.current;
    const mistDigits = buildMistDigits({
      fragranceOn: mistOn,
      fragranceChannels: channels,
      fragranceBlend: blend,
      mistScale: mistScaleRef.current,
    });
    const command = next
      ? mistDigits === "000"
        ? buildAirOnCommand()
        : `ON${mistDigits}`
      : buildAirOffCommand(mistDigits);

    airStatusRef.current = {
      ...airStatusRef.current,
      on: next,
    };
    setAirPurifierOn(next);
    lastFragranceSentRef.current = command;
    enqueueCommands([command], "air", { airPurifierOn: next });
  }, [enqueueCommands, setAirPurifierOn]);

  const cycleAirPurifierMode = useCallback(() => {
    if (!airPurifierOn) {
      return;
    }

    enqueueCommands([AIR_PURIFIER_MODE_COMMAND], "air");
  }, [airPurifierOn, enqueueCommands]);

  /** 꺼져 있으면 켜고, 목표 모드까지 MODE 를 필요한 만큼 보낸다. */
  const selectAirPurifierMode = useCallback(
    (target) => {
      const { on, mode } = airStatusRef.current;
      const commands = [];
      if (!on) {
        const { fragranceOn: mistOn, fragranceChannels: channels, fragranceBlend: blend } =
          fragranceStateRef.current;
        const mistDigits = buildMistDigits({
          fragranceOn: mistOn,
          fragranceChannels: channels,
          fragranceBlend: blend,
          mistScale: mistScaleRef.current,
        });
        commands.push(mistDigits === "000" ? buildAirOnCommand() : `ON${mistDigits}`);
      }
      for (let i = 0; i < getModeStepsToTarget(mode, target); i++) {
        commands.push(AIR_PURIFIER_MODE_COMMAND);
      }
      if (commands.length === 0) {
        return;
      }

      airStatusRef.current = { on: true, mode: target };
      setAirPurifierOn(true);
      setAirPurifierMode(target);
      enqueueCommands(commands, "air", { airPurifierOn: true, airPurifierMode: target });
    },
    [enqueueCommands, setAirPurifierOn, setAirPurifierMode],
  );

  const syncAirPurifierStatus = useCallback(
    (on, mode = 1) => {
      const nextMode = mode >= 1 && mode <= 3 ? mode : 1;
      setAirPurifierOn(Boolean(on));
      setAirPurifierMode(nextMode);
      airStatusRef.current = { on: Boolean(on), mode: nextMode };
      enqueueCommands([buildAirSyncCommand(on, nextMode)], "air", {
        airPurifierOn: Boolean(on),
        airPurifierMode: nextMode,
      });
    },
    [enqueueCommands, setAirPurifierOn, setAirPurifierMode],
  );

  const sendFragranceState = useCallback(
    (nextOn, nextChannels) => {
      const { fragranceBlend: blendValue } = fragranceStateRef.current;
      const { command } = buildFragranceCommand({
        fragranceOn: nextOn,
        fragranceBlend: blendValue,
        fragranceChannels: nextChannels,
        mistScale: mistScaleRef.current,
        airPurifierOn: airStatusRef.current.on,
      });
      lastFragranceSentRef.current = command;
      enqueueCommands([command], "fragrance", {
        fragranceOn: nextOn,
        fragranceChannels: nextChannels,
      });
    },
    [enqueueCommands],
  );

  const applyFragrancePower = useCallback(
    (nextOn) => {
      setFragranceDiffusing?.(false);
      setFragranceDispenseComplete?.(false);
      const nextChannels = Object.fromEntries(
        Object.keys(fragranceStateRef.current.fragranceChannels).map((channel) => [
          channel,
          nextOn,
        ]),
      );
      fragranceStateRef.current = {
        ...fragranceStateRef.current,
        fragranceOn: nextOn,
        fragranceChannels: nextChannels,
        fragranceDiffusing: false,
      };
      setFragranceChannels(nextChannels);
      setFragranceOn(nextOn);
      sendFragranceState(nextOn, nextChannels);
    },
    [
      sendFragranceState,
      setFragranceChannels,
      setFragranceOn,
      setFragranceDiffusing,
      setFragranceDispenseComplete,
    ],
  );

  const toggleFragrancePower = useCallback(() => {
    applyFragrancePower(!fragranceStateRef.current.fragranceOn);
  }, [applyFragrancePower]);

  const stopFragrance = useCallback(() => {
    applyFragrancePower(false);
  }, [applyFragrancePower]);

  const toggleFragranceChannel = useCallback(
    (channel) => {
      setFragranceDiffusing?.(false);
      setFragranceDispenseComplete?.(false);

      const { fragranceOn: isOn, fragranceChannels: prev } =
        fragranceStateRef.current;
      const base = isOn
        ? prev
        : { mint: false, lavender: false, orange: false };
      const nextOn = !isOn || !base[channel];
      const next = { ...base, [channel]: nextOn };
      const nextPower = Object.values(next).some(Boolean);
      fragranceStateRef.current = {
        ...fragranceStateRef.current,
        fragranceOn: nextPower,
        fragranceChannels: next,
        fragranceDiffusing: false,
      };
      setFragranceChannels(next);
      setFragranceOn(nextPower);
      sendFragranceState(nextPower, next);
    },
    [
      sendFragranceState,
      setFragranceChannels,
      setFragranceOn,
      setFragranceDiffusing,
      setFragranceDispenseComplete,
    ],
  );

  const setExternalControl = useCallback((active) => {
    externalControlRef.current = Boolean(active);
  }, []);

  /** 시퀀스용: 원문 명령을 큐로 보내고 공유 상태도 같이 맞춘다. */
  const sendControlCommands = useCallback(
    (commands, stateOverrides = {}) => {
      if (typeof stateOverrides.airPurifierOn === "boolean") {
        airStatusRef.current = {
          ...airStatusRef.current,
          on: stateOverrides.airPurifierOn,
        };
        setAirPurifierOn(stateOverrides.airPurifierOn);
      }
      const lines = (commands ?? []).filter(Boolean);
      lastFragranceSentRef.current = String(lines[lines.length - 1] ?? "");
      if (lines.length === 0) {
        return Promise.resolve(null);
      }

      // 시퀀스가 재부팅 여부를 판단할 수 있게 아두이노 응답(replies)을 그대로 돌려준다.
      const run = async () => {
        try {
          const result = await sendFragranceCommands(
            lines,
            snapshotState(stateOverrides),
          );
          setArduinoConnected(Boolean(result.connected));
          rememberRev(result.state);
          return result;
        } catch (error) {
          setArduinoConnected(false);
          console.warn("[control] send failed", error);
          return null;
        }
      };

      const next = commandQueueRef.current.catch(() => {}).then(run);
      commandQueueRef.current = next;
      return next;
    },
    [rememberRev, setAirPurifierOn, snapshotState],
  );

  return {
    setExternalControl,
    sendControlCommands,
    arduinoConnected,
    reconnectArduino,
    isAirSending,
    isFragranceSending,
    toggleAirPurifier,
    cycleAirPurifierMode,
    selectAirPurifierMode,
    syncAirPurifierStatus,
    toggleFragrancePower,
    toggleFragranceChannel,
    stopFragrance,
  };
}
