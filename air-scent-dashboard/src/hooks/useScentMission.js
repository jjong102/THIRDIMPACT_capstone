import { useCallback, useEffect, useRef, useState } from "react";

import { publishGoAround } from "../services/gdmMap";
import { fetchMqttAirQuality } from "../services/mqttAirQuality";
import {
  clearSharedFragranceSpraying,
  fetchMoveUi,
  putMoveUi,
  setSharedFragranceSpraying,
} from "../services/moveUi";
import { sendFragranceCommands } from "../services/fragranceSerial";
import {
  blendForFragrance,
  channelsForFragrance,
} from "../data/scentRecipes";
import { getWardScent, loadWardScents, loadWards } from "../utils/wardStorage";
import { readingForWard, resolveScentTone } from "../utils/scentVisual";
import { isOdorTrip, clearOdorTrip } from "../utils/odorTrip";
import {
  clearScentReturn,
  isScentReturning,
} from "../utils/scentReturn";
import {
  announcePurifyDoneAndSpray,
  announcePurifyStart,
  announceResumePatrol,
  announceSprayComplete,
  resetMissionAnnouncements,
} from "../utils/robotAnnounce";
import { buildFragranceCommand } from "../utils/fragranceCommand";
import {
  fragranceToTone,
  matchingScentPercent,
  targetPercentForLevel,
} from "../utils/fragranceIntensity";

const POLL_MS = 1000;
const MIN_PURIFY_MS = 5000;
const MIN_SPRAY_MS = 8000;
const SPRAYED_KEY = "air-scent:sprayed-arrival";
const WORK_PHASES = new Set(["purifying", "spraying"]);

function roomsFromPayload(data) {
  const rooms = {};
  for (const [key, value] of Object.entries(data?.rooms ?? {})) {
    if (!value || typeof value !== "object") continue;
    rooms[key] = {
      label: value.label ?? null,
      confidence:
        typeof value.confidence === "number" ? value.confidence : null,
      isOrange: Boolean(value.is_orange ?? value.isOrange),
      tone: value.tone ?? null,
      receivedAt: value.received_at ?? value.receivedAt ?? null,
      raw: value.raw ?? null,
      roomId: value.room_id ?? value.roomId ?? key,
    };
  }
  return rooms;
}

function isFreshReading(reading) {
  return resolveScentTone(reading) === "fresh";
}

function isAtTarget(reading, expectedTone, targetPct) {
  const current = matchingScentPercent(reading, expectedTone);
  if (current == null) return false;
  return current >= targetPct;
}

function receivedAfter(reading, sinceMs) {
  if (!sinceMs) return false;
  const stamp = Date.parse(reading?.receivedAt || "") || 0;
  if (!stamp) return Date.now() - sinceMs > 1500;
  return stamp >= sinceMs - 250;
}

function clearArrivalLock() {
  try {
    window.sessionStorage.removeItem(SPRAYED_KEY);
  } catch {
    // ignore
  }
  resetMissionAnnouncements();
}

/**
 * 와드 도착 → 공기청정만 시작
 * → 오더가 Fresh Air 가 되면 향기 분사
 * → 해당 방 향 목표 % 도달 시 완료 후 초기 위치 복귀
 */
export default function useScentMission({
  mqttAirQuality,
  setAirPurifierOn,
  setFragranceOn,
  setFragranceChannels,
  setFragranceLevel,
  setFragranceBlend,
  setCurrentFragrance,
  setFragranceDiffusing,
  setFragranceDispenseComplete,
  setLastDispenseAt,
}) {
  const [phase, setPhase] = useState("idle");
  const [activeWardId, setActiveWardId] = useState(null);
  const phaseRef = useRef("idle");
  const purifiedAtRef = useRef(0);
  const sprayedAtRef = useRef(0);
  const pendingWardRef = useRef(null);
  const sprayedKeyRef = useRef(null);
  const wardIdRef = useRef(null);
  const returnSeenMovingRef = useRef(false);
  const returnSentAtRef = useRef(0);
  const expectedToneRef = useRef(null);
  const sawSharedSprayRef = useRef(false);
  const targetPctRef = useRef(targetPercentForLevel(2));
  const mqttRef = useRef(mqttAirQuality);
  mqttRef.current = mqttAirQuality;

  useEffect(() => {
    let cancelled = false;

    const setPhaseSafe = (next) => {
      phaseRef.current = next;
      setPhase(next);
    };

    const applyAirPurify = async () => {
      setAirPurifierOn?.(true);
      setFragranceOn(false);
      setFragranceDiffusing(false);
      setFragranceDispenseComplete(false);
      try {
        await sendFragranceCommands(["ON000"], {
          airPurifierOn: true,
          fragranceOn: false,
          fragranceDiffusing: false,
          fragranceChannels: { mint: false, lavender: false, orange: false },
        });
      } catch {
        // Arduino hook retries on reconnect
      }
    };

    const applyWardScent = async (wardId) => {
      const scent = getWardScent(loadWardScents(), wardId);
      const blend = blendForFragrance(scent.fragrance);
      const channels = channelsForFragrance(scent.fragrance);

      expectedToneRef.current = fragranceToTone(scent.fragrance, blend);
      targetPctRef.current = targetPercentForLevel(scent.level);
      setFragranceBlend(blend);
      setFragranceLevel(scent.level);
      setCurrentFragrance?.(scent.fragrance);
      setFragranceChannels(channels);
      setFragranceOn(true);
      setFragranceDiffusing(true);
      setFragranceDispenseComplete(false);
      setLastDispenseAt?.(new Date().toISOString());

      const { command } = buildFragranceCommand({
        fragranceOn: true,
        fragranceBlend: blend,
        fragranceChannels: channels,
        airPurifierOn: true,
      });
      void sendFragranceCommands([command], {
        airPurifierOn: true,
        fragranceOn: true,
        fragranceChannels: channels,
        fragranceBlend: blend,
        fragranceLevel: scent.level,
        fragranceDiffusing: true,
      }).catch(() => {
        // Arduino hook retries on reconnect
      });
      try {
        await setSharedFragranceSpraying(wardId);
        sawSharedSprayRef.current = true;
      } catch {
        // other PCs retry on the next poll
      }
    };

    async function tick() {
      try {
        const ui = await fetchMoveUi();
        if (cancelled) return;

        const navStatus = String(ui?.navStatus || "").toUpperCase();
        const isNavigating = Boolean(ui?.isNavigating);
        const selectedId = ui?.selectedId ? String(ui.selectedId) : null;
        const current = phaseRef.current;
        const odorTrip = isOdorTrip(selectedId);
        const returningHome = isScentReturning() || current === "returning";
        const isScentTrip =
          Boolean(ui?.scentMission) &&
          Boolean(selectedId && !ui?.dropPin) &&
          !odorTrip &&
          !returningHome;
        const arrivalKey = `${selectedId}|${Number(ui?.arrivalId) || 0}`;

        if (odorTrip && (navStatus === "SUCCEEDED" || navStatus === "CANCELED" || navStatus === "ABORTED")) {
          clearOdorTrip();
        }

        const sharedSpraying = Boolean(ui?.fragranceSpraying);
        if (sharedSpraying) {
          sawSharedSprayRef.current = true;
        } else if (sawSharedSprayRef.current && current === "spraying") {
          sawSharedSprayRef.current = false;
          setFragranceOn(false);
          setFragranceDiffusing(false);
          wardIdRef.current = null;
          setActiveWardId(null);
          setPhaseSafe("idle");
          return;
        }

        if (navStatus === "CANCELED" || navStatus === "ABORTED") {
          pendingWardRef.current = null;
          sprayedKeyRef.current = null;
          returnSeenMovingRef.current = false;
          returnSentAtRef.current = 0;
          clearScentReturn();
          if (ui?.scentMission) {
            void putMoveUi({ scentMission: false }).catch(() => {});
          }
          if (current !== "spraying" && sharedSpraying) {
            sawSharedSprayRef.current = false;
            void clearSharedFragranceSpraying().catch(() => {});
          }
          clearArrivalLock();
          if (current !== "idle") {
            wardIdRef.current = null;
            setActiveWardId(null);
            setPhaseSafe("idle");
          }
          return;
        }

        const moving =
          navStatus === "EXECUTING" ||
          (isNavigating && navStatus !== "SUCCEEDED");

        if (returningHome || current === "returning") {
          if (current !== "returning") {
            setPhaseSafe("returning");
          }
          if (moving) {
            returnSeenMovingRef.current = true;
            return;
          }
          const waited = Date.now() - (returnSentAtRef.current || 0);
          const arrived =
            navStatus === "SUCCEEDED" &&
            (returnSeenMovingRef.current || waited >= 4000);
          if (arrived) {
            returnSeenMovingRef.current = false;
            returnSentAtRef.current = 0;
            clearScentReturn();
            setPhaseSafe("done");
            try {
              await putMoveUi({ isNavigating: false });
            } catch {
              // keep polling
            }
          }
          return;
        }

        if (moving) {
          if (isScentTrip) {
            pendingWardRef.current = selectedId;
            if (current === "idle" || current === "done") {
              sprayedKeyRef.current = null;
              clearArrivalLock();
            }
            wardIdRef.current = selectedId;
            setActiveWardId(selectedId);
            if (current !== "navigating" && !WORK_PHASES.has(current)) {
              setPhaseSafe("navigating");
            }
          } else if (
            current !== "idle" &&
            current !== "done" &&
            !WORK_PHASES.has(current) &&
            current !== "returning"
          ) {
            setPhaseSafe("idle");
          }
          if (!WORK_PHASES.has(phaseRef.current)) {
            return;
          }
        }

        let alreadyStarted = sprayedKeyRef.current === arrivalKey;
        if (!alreadyStarted) {
          try {
            alreadyStarted =
              window.sessionStorage.getItem(SPRAYED_KEY) === arrivalKey;
          } catch {
            alreadyStarted = false;
          }
        }
        const shouldPurifyOnArrival =
          navStatus === "SUCCEEDED" &&
          isScentTrip &&
          !WORK_PHASES.has(current) &&
          current !== "returning" &&
          current !== "done" &&
          !alreadyStarted;

        if (shouldPurifyOnArrival) {
          pendingWardRef.current = null;
          sprayedKeyRef.current = arrivalKey;
          try {
            window.sessionStorage.setItem(SPRAYED_KEY, arrivalKey);
          } catch {
            // ignore
          }
          wardIdRef.current = selectedId;
          setActiveWardId(selectedId);
          await applyAirPurify();
          purifiedAtRef.current = Date.now();
          sprayedAtRef.current = 0;
          setPhaseSafe("purifying");
          announcePurifyStart(arrivalKey);
        }

        if (!WORK_PHASES.has(phaseRef.current)) {
          return;
        }

        let rooms = mqttRef.current?.rooms ?? {};
        try {
          const mqtt = await fetchMqttAirQuality();
          if (!cancelled) {
            rooms = roomsFromPayload(mqtt);
          }
        } catch {
          // use last mqtt snapshot
        }

        const wards = loadWards();
        const ward =
          wards.find((item) => item.id === wardIdRef.current) ??
          wards.find((item) => item.id === selectedId) ??
          null;
        const reading = readingForWard(rooms, ward, rooms.default ?? null);

        if (phaseRef.current === "purifying") {
          if (Date.now() - purifiedAtRef.current < MIN_PURIFY_MS) {
            return;
          }
          if (
            isFreshReading(reading) &&
            receivedAfter(reading, purifiedAtRef.current)
          ) {
            if (!ward?.id) return;
            await applyWardScent(ward.id);
            sprayedAtRef.current = Date.now();
            setPhaseSafe("spraying");
            announcePurifyDoneAndSpray(arrivalKey);
          }
          return;
        }

        if (phaseRef.current === "spraying") {
          if (Date.now() - sprayedAtRef.current < MIN_SPRAY_MS) {
            return;
          }
          if (ward?.id) {
            const scent = getWardScent(loadWardScents(), ward.id);
            targetPctRef.current = targetPercentForLevel(scent.level);
            expectedToneRef.current = fragranceToTone(
              scent.fragrance,
              scent.blend
            );
          }
          if (
            isAtTarget(
              reading,
              expectedToneRef.current,
              targetPctRef.current
            ) &&
            receivedAfter(reading, sprayedAtRef.current)
          ) {
            try {
              await publishGoAround();
            } catch {
              // retry next tick
              return;
            }
            if (cancelled) return;
            setFragranceOn(false);
            setFragranceDiffusing(false);
            setFragranceDispenseComplete(true);
            sawSharedSprayRef.current = false;
            void clearSharedFragranceSpraying().catch(() => {});
            announceSprayComplete(arrivalKey);
            announceResumePatrol(arrivalKey);
            clearScentReturn();
            returnSeenMovingRef.current = false;
            returnSentAtRef.current = 0;
            setPhaseSafe("done");
            try {
              await putMoveUi({
                isNavigating: false,
                scentMission: false,
              });
            } catch {
              // patrol resume already published
            }
          }
        }
      } catch {
        // keep last phase
      }
    }

    tick();
    const timer = window.setInterval(tick, POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [
    setAirPurifierOn,
    setCurrentFragrance,
    setFragranceBlend,
    setFragranceChannels,
    setFragranceDiffusing,
    setFragranceDispenseComplete,
    setFragranceLevel,
    setFragranceOn,
    setLastDispenseAt,
  ]);

  const abort = useCallback(() => {
    phaseRef.current = "idle";
    pendingWardRef.current = null;
    wardIdRef.current = null;
    purifiedAtRef.current = 0;
    sprayedAtRef.current = 0;
    returnSeenMovingRef.current = false;
    returnSentAtRef.current = 0;
    sawSharedSprayRef.current = false;
    clearScentReturn();
    clearArrivalLock();
    void clearSharedFragranceSpraying().catch(() => {});
    setActiveWardId(null);
    setPhase("idle");
  }, []);

  return {
    phase,
    wardId: activeWardId,
    active: phase !== "idle" && phase !== "done",
    abort,
  };
}
