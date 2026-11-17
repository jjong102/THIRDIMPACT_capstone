import { ChevronDown, ChevronUp } from "lucide-react";
import { useEffect, useState } from "react";

import { AIR_PURIFIER_MODE_LABELS } from "../utils/airPurifierCommand";
import "./StatusMiniPanel.css";

const FRAGRANCE_API = import.meta.env.VITE_FRAGRANCE_API ?? "";
const MQTT_API = import.meta.env.VITE_MQTT_API ?? "";
const POLL_MS = 2000;
const LOG_LINES = 4;
const COLLAPSE_KEY = "air-scent-status-mini-collapsed";
const STALE_SEC = 60; // 와드는 약 10초마다 보냄

const CHANNELS = [
  { id: "mint", emoji: "🌿", label: "민트" },
  { id: "lavender", emoji: "💜", label: "라벤더" },
  { id: "orange", emoji: "🍊", label: "오렌지" },
];
const TIMERS = [
  { seconds: 0, label: "계속" },
  { seconds: 30, label: "30초" },
  { seconds: 60, label: "60초" },
];

/** 로그 시각 "HH:MM:SS" → 지금부터 몇 초 전 (자정 넘김은 하루 빼서) */
function secondsAgo(hms) {
  const m = /^(\d{1,2}):(\d{2}):(\d{2})$/.exec(String(hms ?? ""));
  if (!m) return null;
  const now = new Date();
  const t = new Date(now);
  t.setHours(Number(m[1]), Number(m[2]), Number(m[3]), 0);
  let diff = (now - t) / 1000;
  if (diff < -60) diff += 86400;
  return Math.max(0, diff);
}

/** 미니 패널용 짧은 줄: 와드 이름·배터리 빼기 (배터리는 왼쪽 와드 카드에) */
function shortLine(text) {
  return String(text).replace(/^ward-\d+ · /, "").replace(/ · 배터리 [^·]+$/, "");
}

function loadCollapsed() {
  try {
    return window.localStorage.getItem(COLLAPSE_KEY) === "1";
  } catch {
    return false;
  }
}

async function getJson(url) {
  const response = await fetch(url);
  return response.ok ? response.json() : null;
}

/**
 * 메인 지도 오른쪽 위(전체화면 버튼 밑) 작은 현황 — 더보기의 '제어'(청정기·발향·아두이노)와
 * '시연'(수신 로그 = 와드 판정)을 한 곳에. 밑에 청정기·향 켜고 끄기, 분무 시간 버튼.
 * 조작은 더보기 제어 탭과 같은 App 함수(useArduinoControllers)를 쓴다.
 */
export default function StatusMiniPanel({
  airPurifierOn: purifierProp,
  airPurifierSending = false,
  onToggleAirPurifier,
  fragranceOn: fragranceOnProp,
  fragranceChannels: channelsProp,
  onToggleFragranceChannel,
  onStopFragrance,
}) {
  const [collapsed, setCollapsed] = useState(loadCollapsed);
  const [device, setDevice] = useState(null);      // fragrance_bridge 공유 상태 (props 없을 때 표시용)
  const [arduino, setArduino] = useState(null);
  const [logs, setLogs] = useState([]);
  const [sprayTimer, setSprayTimer] = useState(0);
  const [sprayEndsAt, setSprayEndsAt] = useState(null);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      const [state, health, mqtt] = await Promise.allSettled([
        getJson(`${FRAGRANCE_API}/api/fragrance/state`),
        getJson(`${FRAGRANCE_API}/api/fragrance/health`),
        getJson(`${MQTT_API}/api/mqtt/air-quality`),
      ]);
      if (cancelled) return;
      if (state.status === "fulfilled" && state.value) setDevice(state.value.state ?? state.value);
      if (health.status === "fulfilled") setArduino(health.value ? Boolean(health.value.connected) : false);
      if (mqtt.status === "fulfilled" && mqtt.value) {
        const lines = (mqtt.value.logs ?? []).filter(
          (line) => (line?.kind === "mqtt" || line?.kind === "demo") && line?.text
        );
        setLogs(lines.slice(-LOG_LINES));
      }
    };
    load();
    const timer = window.setInterval(load, POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, []);

  const purifierOn = purifierProp ?? Boolean(device?.airPurifierOn);
  const fragranceOn = fragranceOnProp ?? Boolean(device?.fragranceOn);
  const channels = fragranceOn ? channelsProp ?? device?.fragranceChannels ?? {} : {};
  const modeLabel = String(AIR_PURIFIER_MODE_LABELS[device?.airPurifierMode] ?? "").replace("모드", "");

  // 분무 시간: 꺼지면 타이머도 지움, 시간이 되면 분무만 끔
  useEffect(() => {
    if (!fragranceOn) setSprayEndsAt(null);
  }, [fragranceOn]);
  useEffect(() => {
    if (!sprayEndsAt) return undefined;
    const timer = window.setInterval(() => {
      const t = Date.now();
      setNow(t);
      if (t >= sprayEndsAt) {
        setSprayEndsAt(null);
        onStopFragrance?.();
      }
    }, 500);
    return () => window.clearInterval(timer);
  }, [sprayEndsAt, onStopFragrance]);
  const sprayLeft = sprayEndsAt ? Math.max(0, Math.ceil((sprayEndsAt - now) / 1000)) : null;

  const toggleChannel = (id) => {
    const turningOn = !(fragranceOn && channels[id]);
    onToggleFragranceChannel?.(id);
    if (turningOn && sprayTimer > 0 && !sprayEndsAt) {
      setNow(Date.now());
      setSprayEndsAt(Date.now() + sprayTimer * 1000);
    }
  };
  const chooseTimer = (seconds) => {
    setSprayTimer(seconds);
    if (seconds > 0 && fragranceOn) {
      setNow(Date.now());
      setSprayEndsAt(Date.now() + seconds * 1000);
    } else {
      setSprayEndsAt(null);
    }
  };

  const toggleCollapsed = (event) => {
    event.stopPropagation();
    const next = !collapsed;
    setCollapsed(next);
    try {
      window.localStorage.setItem(COLLAPSE_KEY, next ? "1" : "0");
    } catch {
      // 저장 못 해도 화면은 동작
    }
  };

  const lastAgo = logs.length ? secondsAgo(logs[logs.length - 1].time) : null;
  const stale = lastAgo == null || lastAgo > STALE_SEC;
  const canControl = Boolean(onToggleFragranceChannel);

  return (
    <section
      className={`status-mini${collapsed ? " is-collapsed" : ""}`}
      aria-label="현황"
      onPointerDown={(event) => event.stopPropagation()}
    >
      <header className="status-mini-head">
        <strong>현황</strong>
        <span
          className={`status-mini-dot ${arduino ? "is-on" : "is-off"}`}
          title={arduino ? "아두이노 연결됨" : "아두이노 연결 안 됨"}
        />
        <span className={`status-mini-chip ${purifierOn ? "is-on" : ""}`}>
          청정기 {purifierOn ? `ON${modeLabel ? `·${modeLabel}` : ""}` : "OFF"}
        </span>
        <button
          type="button"
          className="status-mini-toggle"
          onClick={toggleCollapsed}
          aria-label={collapsed ? "현황 펼치기" : "현황 접기"}
        >
          {collapsed ? <ChevronDown size={14} /> : <ChevronUp size={14} />}
        </button>
      </header>

      {!collapsed ? (
        <>
          <div className="status-mini-log" aria-label="수신 로그">
            {logs.length && stale ? (
              <p className="status-mini-stale">
                와드 수신 없음 · {lastAgo >= 120 ? `${Math.floor(lastAgo / 60)}분째` : `${Math.round(lastAgo)}초째`}
              </p>
            ) : null}
            {logs.length ? (
              logs.map((line) => (
                <p key={line.id ?? `${line.time}-${line.text}`} className={`status-mini-line kind-${line.kind}`}>
                  <time>{line.time}</time>
                  <span>{shortLine(line.text)}</span>
                </p>
              ))
            ) : (
              <p className="status-mini-muted">와드 판정 수신 대기 중…</p>
            )}
          </div>

          {canControl ? (
            <div className="status-mini-controls" aria-label="조작">
              <div className="status-mini-ctl-row">
                <span className="status-mini-key">청정기</span>
                <button
                  type="button"
                  className={`status-mini-btn${purifierOn ? " is-on" : ""}`}
                  aria-pressed={purifierOn}
                  disabled={airPurifierSending}
                  onClick={() => onToggleAirPurifier?.()}
                >
                  {airPurifierSending ? "전송 중" : purifierOn ? "ON · 끄기" : "OFF · 켜기"}
                </button>
              </div>
              <div className="status-mini-ctl-row">
                <span className="status-mini-key">향</span>
                {CHANNELS.map((channel) => {
                  const on = Boolean(channels[channel.id]);
                  return (
                    <button
                      key={channel.id}
                      type="button"
                      className={`status-mini-btn scent-${channel.id}${on ? " is-on" : ""}`}
                      aria-pressed={on}
                      onClick={() => toggleChannel(channel.id)}
                      title={`${channel.label} ${on ? "끄기" : "켜기"}`}
                    >
                      {channel.emoji} {channel.label}
                    </button>
                  );
                })}
              </div>
              <div className="status-mini-ctl-row">
                <span className="status-mini-key">분무</span>
                {TIMERS.map((option) => (
                  <button
                    key={option.seconds}
                    type="button"
                    className={`status-mini-btn${sprayTimer === option.seconds ? " is-selected" : ""}`}
                    aria-pressed={sprayTimer === option.seconds}
                    onClick={() => chooseTimer(option.seconds)}
                  >
                    {option.label}
                  </button>
                ))}
                {sprayLeft != null ? <span className="status-mini-left">{sprayLeft}초 뒤 꺼짐</span> : null}
              </div>
            </div>
          ) : null}
        </>
      ) : null}
    </section>
  );
}
