import { useEffect, useRef } from "react";

import { readingForWard, isOdorReading } from "../utils/scentVisual";
import { loadWards, wardDisplayName } from "../utils/wardStorage";

function findLivingRoom(wards) {
  const list = Array.isArray(wards) ? wards : [];
  return (
    list.find((ward) => wardDisplayName(ward) === "거실") ||
    list.find((ward) => {
      const id = String(ward?.id ?? "")
        .toLowerCase()
        .replace(/-/g, "_");
      return id === "room_1" || id === "room1" || id === "ward_home_1";
    }) ||
    list[0] ||
    null
  );
}

function livingRoomReading(mqttAirQuality, living) {
  const fallback =
    mqttAirQuality?.hasData || mqttAirQuality?.label
      ? {
          label: mqttAirQuality.label,
          confidence: mqttAirQuality.confidence,
          tone: mqttAirQuality.tone,
          raw: mqttAirQuality.label,
          demo: Boolean(mqttAirQuality.demo),
        }
      : null;
  return readingForWard(mqttAirQuality?.rooms, living, fallback);
}

export default function useOdorResponse({ mqttAirQuality, goToWard, ready = true }) {
  const busyRef = useRef(false);
  const movedRef = useRef(false);
  const clearTicksRef = useRef(0);
  const mqttRef = useRef(mqttAirQuality);
  const goToWardRef = useRef(goToWard);
  mqttRef.current = mqttAirQuality;
  goToWardRef.current = goToWard;

  useEffect(() => {
    if (!ready) return undefined;

    let cancelled = false;

    const tick = async () => {
      if (cancelled || busyRef.current) return;

      const living = findLivingRoom(loadWards());
      const reading = livingRoomReading(mqttRef.current, living);

      if (!isOdorReading(reading)) {
        clearTicksRef.current += 1;
        if (clearTicksRef.current >= 12) {
          movedRef.current = false;
        }
        return;
      }

      clearTicksRef.current = 0;
      if (!living || movedRef.current) return;

      busyRef.current = true;
      movedRef.current = true;
      try {
        await goToWardRef.current?.(living, { scent: true });
      } catch {
        movedRef.current = false;
      } finally {
        busyRef.current = false;
      }
    };

    tick();
    const timer = window.setInterval(tick, 1000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [ready]);
}
