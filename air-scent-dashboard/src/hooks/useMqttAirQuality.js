import { useEffect, useState } from "react";

import { fetchMqttAirQuality, fetchMqttHealth } from "../services/mqttAirQuality";
import { displayWardScentLabel, resolveScentTone, KITCHEN_DEMO_READING } from "../utils/scentVisual";

// MQTT는 ~10초 주기. 브리지가 마지막 값을 캐시하므로
// 자주 폴링해도 MQTT를 다시 읽진 않지만, 빈 응답으로 상태를 지우지 않게 함.
const POLL_MS = 800;

const initialState = {
  connected: false,
  mqttConnected: false,
  label: null,
  confidence: null,
  isOrange: false,
  tone: "unknown",
  receivedAt: null,
  error: null,
  hasData: false,
  roomId: "default",
  demo: false,
  rooms: {},
  logs: [],
};

function normalizeRoom(entry) {
  if (!entry || typeof entry !== "object") return null;
  const label = displayWardScentLabel(entry.label) ?? entry.label ?? null;
  const isOrange = Boolean(entry.is_orange ?? entry.isOrange);
  const tone =
    entry.tone && entry.tone !== "unknown"
      ? entry.tone
      : resolveScentTone({ label, isOrange, raw: entry.raw });
  return {
    label,
    confidence:
      typeof entry.confidence === "number" ? entry.confidence : null,
    isOrange,
    tone,
    receivedAt: entry.received_at ?? entry.receivedAt ?? null,
    raw: entry.raw ?? null,
    roomId: entry.room_id ?? entry.roomId ?? null,
    topic: entry.topic ?? null,
    demo: Boolean(entry.demo),
  };
}

function buildState(data, health = null) {
  const roomsRaw = data.rooms ?? {};
  const rooms = {};
  for (const [key, value] of Object.entries(roomsRaw)) {
    const normalized = normalizeRoom(value);
    if (normalized) rooms[key] = normalized;
  }

  rooms.room_2 = {
    ...KITCHEN_DEMO_READING,
    receivedAt: new Date().toISOString(),
  };
  rooms.room2 = rooms.room_2;

  const label = displayWardScentLabel(data.label) ?? data.label ?? null;
  const isOrange = Boolean(data.is_orange);
  const tone =
    data.tone && data.tone !== "unknown"
      ? data.tone
      : resolveScentTone({
          label,
          isOrange,
          raw: data.raw,
        });

  const mqttConnected = Boolean(
    health?.connected ?? data.mqtt_connected ?? data.mqttConnected
  );

  return {
    connected: Boolean(data.connected),
    mqttConnected,
    label,
    confidence: typeof data.confidence === "number" ? data.confidence : null,
    isOrange,
    tone,
    receivedAt: data.received_at ?? data.receivedAt ?? null,
    error: data.error ?? health?.error ?? null,
    hasData: Boolean(data.raw) || Object.keys(rooms).length > 0,
    roomId: data.room_id ?? "default",
    demo: Boolean(data.demo),
    rooms,
    logs: Array.isArray(data.logs) ? data.logs : [],
  };
}

/** 10초 주기 사이·브리지 재시작 직후에도 마지막 Fresh Air 등을 유지 */
function mergeSticky(prev, next) {
  const logs = next.logs?.length ? next.logs : prev.logs;

  if (next.hasData) return { ...next, logs };

  if (!prev.hasData) return { ...next, logs };

  return {
    ...next,
    label: prev.label,
    confidence: prev.confidence,
    isOrange: prev.isOrange,
    tone: prev.tone,
    receivedAt: prev.receivedAt,
    hasData: true,
    roomId: prev.roomId,
    rooms:
      Object.keys(next.rooms).length > 0 ? next.rooms : prev.rooms,
    logs,
  };
}

export default function useMqttAirQuality() {
  const [airQuality, setAirQuality] = useState(initialState);

  useEffect(() => {
    let cancelled = false;

    const poll = async () => {
      try {
        const [data, health] = await Promise.all([
          fetchMqttAirQuality(),
          fetchMqttHealth().catch(() => null),
        ]);
        if (cancelled) return;

        const next = buildState(data, health);
        setAirQuality((prev) => mergeSticky(prev, next));
      } catch (error) {
        if (cancelled) return;

        // 네트워크 순간 실패 시에도 마지막 분류값 유지
        setAirQuality((prev) => ({
          ...prev,
          connected: false,
          mqttConnected: false,
          error: error.message,
        }));
      }
    };

    poll();
    const timer = setInterval(poll, POLL_MS);

    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);

  return airQuality;
}
