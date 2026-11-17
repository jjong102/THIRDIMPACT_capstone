import { ChevronLeft, ChevronRight } from "lucide-react";
import { useEffect, useState } from "react";

import "./WardScentPanel.css";

const API_BASE = import.meta.env.VITE_GDM_API ?? "";
const POLL_MS = 2000;
const BATT_LOW_V = 3.6; // 이 아래면 빨간색 (10/26~27 방전 리셋은 약 3.4~3.5V 부근부터)
const STALE_SEC = 60; // 와드는 약 10초마다 보냄. 60초 넘게 없으면 연결 끊김 (INTERFACE.md)

// ward_view.py 와 같은 이름·상태 표기
const LABEL_KO = {
  Peppermint: "페퍼민트",
  Lavender: "라벤더",
  Orange: "오렌지",
  Vinegar: "식초",
  Unknown: "모르는 냄새",
};
const STATE_KO = {
  WARMUP: "예열 중",
  CLEAN: "무향",
  SCENT: "향기",
  MALODOR: "악취",
  UNKNOWN: "모르는 냄새",
  HOLD: "보류",
};
const SCENT_KO = { peppermint: "페퍼민트", mint: "페퍼민트", lavender: "라벤더", orange: "오렌지", mix: "3가지 섞어서" };
// 막대 색 = 냄새 이름 (validate_palette.js 통과). 글자는 늘 함께 적어 색만으로 구분하지 않는다.
const TONE = {
  Peppermint: "mint",
  Lavender: "lavender",
  Orange: "orange",
  Vinegar: "odor",
  Unknown: "odor",
};

function displayName(entry) {
  const state = entry?.state;
  if (state === "WARMUP") return "예열 중";
  const name = LABEL_KO[entry?.label];
  if (!name) return STATE_KO[state] || "—";
  // 우리 향 3종은 센서로 1~4° 차이라 이름은 추정 (확률 % 는 붙이지 않는다)
  return state === "SCENT" ? `${name}(추정)` : name;
}

function toneOf(entry) {
  if (entry?.state === "CLEAN" || entry?.state === "WARMUP") return "fresh";
  return TONE[entry?.label] || "fresh";
}

function Bar({ pct, tone }) {
  const value = Math.max(0, Math.min(100, Number(pct) || 0));
  return (
    <span className="ward-bar-track">
      {value > 0 ? (
        <span className={`ward-bar-fill tone-${tone}`} style={{ width: `${value}%` }} />
      ) : null}
    </span>
  );
}

/** 와드 배터리 — 펌웨어(11/15)가 보내는 전압으로 어림한 % (±10~15%). USB 연결 중엔 4.2V 근처 */
function Battery({ entry }) {
  const v = Number(entry?.batt_v);
  if (!Number.isFinite(v) || v < 0) return null;
  const pct = Math.max(0, Math.min(100, Number(entry?.batt_pct) || 0));
  const low = v < BATT_LOW_V;
  return (
    <span
      className={`ward-batt${low ? " is-low" : ""}`}
      title={`배터리 ${v.toFixed(2)}V — 전압으로 어림한 값(±10~15%)${low ? " · 충전 필요" : ""}`}
    >
      <span className="ward-batt-icon" aria-hidden="true">
        <span className="ward-batt-level" style={{ width: `${pct}%` }} />
      </span>
      {low ? "충전 " : ""}
      {pct}%
    </span>
  );
}

/** 와드 냄새 판정 — ward_view 처럼 지금 판정을 세기 % 가로 막대로 (반투명 유리 카드) */
export default function WardScentPanel() {
  const [ward, setWard] = useState(null);
  const [wardOnline, setWardOnline] = useState(null); // 11/17 펌웨어 status (LWT)
  /* 오른쪽 목적지 카드와 같은 접기: 접으면 왼쪽 가장자리에 좁은 탭(같은 높이·같은 바닥 위치) */
  const [collapsed, setCollapsed] = useState(false);
  const [dock, setDock] = useState({ height: null, bottom: 0 });
  const collapse = (event) => {
    event.stopPropagation();
    const node = event.currentTarget.closest(".ward-scent-panel");
    if (node) {
      const rect = node.getBoundingClientRect();
      setDock({ height: rect.height, bottom: Math.max(0, Math.round(window.innerHeight - rect.bottom)) });
    }
    setCollapsed(true);
  };
  const expand = () => setCollapsed(false);
  const [now, setNow] = useState(() => Date.now() / 1000);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const response = await fetch(`${API_BASE}/api/nav/ui`);
        const payload = response.ok ? await response.json() : null;
        if (cancelled) return;
        setWard(payload?.state?.wardState ?? null);
        setWardOnline(payload?.state?.wardOnline ?? null);
      } catch {
        // 직전 값을 유지한다
      }
      if (!cancelled) setNow(Date.now() / 1000);
    };
    load();
    const timer = window.setInterval(load, POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, []);

  const age = ward?.rx_time ? now - ward.rx_time : null;
  // 브로커가 '끊김'을 알려 왔고 그 뒤로 판정이 없으면 60초를 기다리지 않고 바로 끊김
  const offline =
    wardOnline?.online === false && (!ward?.rx_time || wardOnline.at >= ward.rx_time);
  const stale = offline || age == null || age > STALE_SEC;
  const current = ward && !stale ? ward : null;
  const state = current?.state;
  const statusTone =
    state === "MALODOR" || state === "UNKNOWN" ? "bad" : state === "SCENT" ? "scent" : "ok";

  return (
    <section
      className={`ward-scent-panel${collapsed ? " is-collapsed" : ""}`}
      aria-label="와드 판정"
      style={collapsed && dock.height ? { height: dock.height, bottom: dock.bottom } : undefined}
      onClick={collapsed ? expand : undefined}
    >
      {collapsed ? (
        <button
          type="button"
          className="slam-icon-btn ward-scent-toggle"
          onClick={(event) => {
            event.stopPropagation();
            expand();
          }}
          aria-label="와드 열기"
        >
          <ChevronRight size={18} />
        </button>
      ) : null}
      <header className="ward-scent-head">
        <strong>와드</strong>
        {current ? <Battery entry={current} /> : null}
        {stale ? (
          <span className="ward-status is-off">연결 끊김</span>
        ) : (
          <span className={`ward-status is-${statusTone}`}>{STATE_KO[state] || state || "—"}</span>
        )}
        {current?.event === "ON" ? <span className="ward-status is-bad">출동</span> : null}
        <button
          type="button"
          className="slam-icon-btn ward-scent-hide"
          onClick={collapse}
          aria-label="와드 숨기기"
        >
          <ChevronLeft size={16} />
        </button>
      </header>

      {current && state !== "WARMUP" ? (
        <div
          className="ward-scent-now"
          title={`표 ${current.votes ?? "—"}/${current.n ?? "—"} · 청정기 권고 ${current.purifier === "on" ? "ON" : "off"}`}
        >
          <div className="ward-scent-now-top">
            <span className="ward-scent-name">{displayName(current)}</span>
            <span className="ward-scent-pct">{Number(current.strength_pct) || 0}%</span>
          </div>
          <Bar pct={current.strength_pct} tone={toneOf(current)} />
          {current.spraying ? (
            <p className="ward-scent-spray">
              지금 뿌리는 향: {SCENT_KO[String(current.spraying).toLowerCase()] || current.spraying}
            </p>
          ) : null}
        </div>
      ) : (
        <p className="ward-scent-empty">
          {stale
            ? offline
              ? wardOnline?.sleep
                ? "와드가 꺼져 있습니다 (켜려면 와드 RESET 버튼)"
                : "와드 연결이 끊겼습니다 (전원·핫스팟 확인)"
              : "60초 넘게 와드 판정이 오지 않았습니다"
            : "센서 예열 중입니다"}
        </p>
      )}

    </section>
  );
}
