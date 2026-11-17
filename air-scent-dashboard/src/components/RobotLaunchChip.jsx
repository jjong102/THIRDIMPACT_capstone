import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { CarFront } from "lucide-react";

import "./RobotLaunchChip.css";

const API_BASE = import.meta.env.VITE_GDM_API ?? "";
const POLL_MS = 2000;

const LABELS = {
  off: "주행 꺼짐",
  starting: "주행 켜지는 중",
  on: "주행 켜짐",
  stopping: "주행 꺼지는 중",
  unknown: "주행 확인 불가",
};

const FORCE_AFTER_SEC = 15;

// 상태별로 누르면 할 일
function actionFor(launch) {
  const state = launch?.state;
  if (state === "off") {
    return {
      action: "start",
      hint: "눌러서 켜기",
      question: "주행을 켤까요?",
      note: "켜지면 로봇이 정찰을 시작해 움직일 수 있어요.",
      confirm: "켜기",
    };
  }
  if (state === "on" || state === "starting") {
    return {
      action: "stop",
      hint: "눌러서 끄기",
      question: "주행을 끌까요?",
      note: "주행 Jetson 의 launch 에 Ctrl+C 를 보냅니다. 터미널에서 켠 것도 꺼져요.",
      confirm: "끄기",
    };
  }
  if (state === "stopping" && (launch?.stopping_for_s ?? 0) >= FORCE_AFTER_SEC) {
    return {
      action: "force_stop",
      hint: "멈춤 · 눌러서 강제 끄기",
      question: "강제로 끌까요?",
      note: `${Math.round(launch.stopping_for_s)}초째 꺼지는 중입니다. launch 와 노드를 강제로 종료합니다.`,
      confirm: "강제 끄기",
    };
  }
  return null;
}

async function sendLaunchAction(action) {
  const response = await fetch(`${API_BASE}/api/nav/robot_launch`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action }),
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok || payload?.ok === false) {
    throw new Error(payload?.error || `HTTP ${response.status}`);
  }
  return payload;
}

function formatAge(sec) {
  if (sec == null || !Number.isFinite(sec)) return null;
  if (sec < 60) return `${Math.round(sec)}초 전 시작`;
  if (sec < 3600) return `${Math.round(sec / 60)}분 전 시작`;
  return `${(sec / 3600).toFixed(1)}시간 전 시작`;
}

/**
 * 주행 Jetson 의 `ros2 launch third_impact impact_launch.py` 상태.
 * 꺼짐 / 켜지는 중 / 켜짐 / 꺼지는 중 (서버가 2초마다 읽기만 함)
 */
export default function RobotLaunchChip({ className = "" }) {
  const [launch, setLaunch] = useState(null);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [popPos, setPopPos] = useState(null);
  const btnRef = useRef(null);

  // 확인 창은 body 에 띄운다 (오른쪽 아래 패널이 overflow 로 잘라 안 보이던 문제)
  const openPop = () => {
    const rect = btnRef.current?.getBoundingClientRect();
    if (rect) {
      const left = Math.max(8, Math.min(rect.left, window.innerWidth - 328));
      const below = rect.bottom + 8;
      const openBelow = rect.top < 180 || window.innerHeight - rect.bottom > rect.top;
      setPopPos(
        openBelow
          ? { left, top: below }
          : { left, bottom: window.innerHeight - rect.top + 8 }
      );
    }
    setError(null);
    setOpen(true);
  };

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const response = await fetch(`${API_BASE}/api/nav/robot_launch`);
        const payload = response.ok ? await response.json() : null;
        if (!cancelled) setLaunch(payload);
      } catch {
        if (!cancelled) setLaunch(null);
      }
    };
    load();
    const timer = window.setInterval(load, POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, []);

  const state = launch?.state || "unknown";
  const act = actionFor(launch);

  const runAction = async () => {
    if (!act || busy) return;
    setBusy(true);
    setError(null);
    try {
      await sendLaunchAction(act.action);
      setOpen(false);
    } catch (err) {
      setError(err?.message || "실패했습니다");
    } finally {
      setBusy(false);
    }
  };

  const warn = state === "on" && Boolean(launch?.warning || launch?.ui_link === false);
  const lines = [
    launch?.detail,
    launch?.warning,
    state === "on" && launch?.ui_link === false
      ? "UI 에서 주행 노드가 보이지 않습니다 (DDS 연결 확인)"
      : null,
    state === "on" && launch?.ui_link === true ? "UI 와 연결됨" : null,
    launch?.network ? `network:=${launch.network}` : null,
    formatAge(launch?.age_s),
  ].filter(Boolean);

  return (
    <div className={`robot-launch ${className}`}>
      <button
        type="button"
        className={`robot-launch-btn is-${state}${warn ? " is-warn" : ""}`}
        ref={btnRef}
        onClick={() => (open ? setOpen(false) : openPop())}
        aria-expanded={open}
        disabled={busy}
      >
        <CarFront size={18} />
        <span className="robot-launch-dot" aria-hidden="true" />
        <span className="robot-launch-text">
          <span>
            {busy ? "보내는 중…" : LABELS[state] || state}
            {warn ? <em> !</em> : null}
          </span>
          {act && !busy ? <small>{act.hint}</small> : null}
        </span>
      </button>
      {open && popPos ? createPortal(
        <>
        <div className="robot-launch-backdrop" onPointerDown={() => setOpen(false)} />
        <div
          className="robot-launch-pop"
          role="dialog"
          style={{
            left: `${popPos.left}px`,
            ...(popPos.top != null
              ? { top: `${popPos.top}px` }
              : { bottom: `${popPos.bottom}px` }),
          }}
          onPointerDown={(event) => event.stopPropagation()}
        >
          {act ? (
            <>
              <p className="robot-launch-q">{act.question}</p>
              <p className="robot-launch-note">{act.note}</p>
            </>
          ) : null}
          {lines.map((line) => (
            <p key={line}>{line}</p>
          ))}
          <p className="robot-launch-cmd">
            ros2 launch third_impact impact_launch.py network:=jetson
          </p>
          {error ? <p className="robot-launch-error">{error}</p> : null}
          <div className="robot-launch-actions">
            <button type="button" className="robot-launch-cancel" onClick={() => setOpen(false)}>
              {act ? "취소" : "닫기"}
            </button>
            {act ? (
              <button
                type="button"
                className={`robot-launch-confirm is-${act.action}`}
                onClick={runAction}
                disabled={busy}
              >
                {busy ? "보내는 중…" : act.confirm}
              </button>
            ) : null}
          </div>
        </div>
        </>,
        document.body
      ) : null}
    </div>
  );
}
