import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowLeft, Bot, Eye, EyeOff, MapPin, Mic, MicOff, RefreshCw, Settings, Wifi, WifiHigh, WifiLow, WifiOff, WifiZero, Wind, SprayCan } from "lucide-react";

import { useCurrentTime } from "../hooks/useCurrentTime";
import { useSttHealth } from "../hooks/useSttHealth";
import { useWifiStatus } from "../hooks/useWifiStatus";
import { fetchMoveUi } from "../services/moveUi";
import "./Header.css";

const SCREENSAVER_LONG_PRESS_MS = 650;

const LOCATING_STATES = ["VERIFYING", "SEARCHING", "LOCAL_RECHECK", "WAIT_STOP"];
const SCENT_NAMES = { mint: "페퍼민트", lavender: "라벤더", orange: "오렌지" };

// 주행 노드가 보내는 영어 detail 을 짧은 한국어로
function koreanDetail(detail) {
  const text = String(detail || "").trim();
  if (!text) return null;
  if (/^Paused/i.test(text)) return "일시정지 · 위치·센서 준비 대기";
  return text;
}

function remainSec(until) {
  const left = Math.round((Number(until) || 0) - Date.now() / 1000);
  return left > 0 ? `${left}초 남음` : null;
}

/**
 * 왼쪽 위 로봇 상태. 실제로 일어나는 일의 우선순위대로 하나만 고른다.
 * 주행 프로그램 → 수동 → 도착 후 청정·발향 → 설정 → 위치 추정 → 센서 → 임무 단계
 */
function describeRobot(ui) {
  if (!ui) return { label: "서버 연결 안 됨", detail: "대시보드 서버 응답 없음", tone: "off" };

  const launch = ui.robotLaunch;
  const ls = launch?.state;
  if (ls === "off") return { label: "주행 꺼짐", detail: "주행 프로그램이 실행 중이 아닙니다", tone: "off" };
  if (ls === "starting") {
    const missing = launch.missing?.length ? `준비 중: ${launch.missing.join(", ")}` : "노드 켜는 중";
    return { label: "주행 켜는 중", detail: missing, tone: "busy" };
  }
  if (ls === "stopping") return { label: "주행 끄는 중", detail: "노드를 내리는 중", tone: "busy" };
  if (ls === "unknown") return { label: "주행 연결 안 됨", detail: launch?.detail || null, tone: "off" };
  if (ls === "on" && launch.ui_link === false) {
    return {
      label: "주행 연결 안 됨",
      detail: launch.warning || "UI 에서 주행 노드가 보이지 않습니다",
      tone: "warn",
    };
  }

  if (ui.manualControl || ui.missionPhase === "manual") {
    return { label: "수동 조작", detail: "조이스틱으로 조작 중", tone: "warn" };
  }

  const seq = ui.arrivalSequence;
  if (seq?.phase === "purify") {
    return { label: "청정 중", detail: ["도착 후 공기청정", remainSec(seq.until)].filter(Boolean).join(" · "), tone: "run" };
  }
  if (seq?.phase === "spray") {
    const scent = SCENT_NAMES[seq.scent] || seq.scent;
    return {
      label: "발향 중",
      detail: [scent ? `${scent} 향` : null, remainSec(seq.until)].filter(Boolean).join(" · "),
      tone: "scent",
    };
  }

  if (ui.robotMode === "teach" || ui.missionPhase === "setup") {
    return { label: "설정 모드", detail: "목표·구역 설정 (ROS)", tone: "warn" };
  }

  if (ui.poseEstimating) return { label: "위치 지정 중", detail: "지도에 로봇 위치를 표시하세요", tone: "busy" };

  // 로봇이 임무 가능(ready)하거나 실제로 주행 중이면, 위치 상태 이름과 상관없이 임무를 보여 준다.
  // (DEGRADED 처럼 READY 가 아니어도 로봇은 ready=true 로 정찰한다)
  const loc = ui.localizationState;
  const degraded = loc === "DEGRADED" ? "위치 일시 불일치 확인 중" : null;
  if (ui.missionReady === true || ui.navActive) {
    if (ui.missionPhase === "return") return { label: "목표 복귀", detail: degraded || "와드 쪽으로 이동 중", tone: "run" };
    if (ui.missionPhase === "wait") return { label: "목표 대기", detail: degraded || "목표 위치에 도착", tone: "run" };
    if (ui.missionPhase === "patrol") return { label: "정찰 중", detail: degraded, tone: "run" };
  }

  if (LOCATING_STATES.includes(loc)) return { label: "위치 찾는 중", detail: "LiDAR 와 지도를 맞추는 중", tone: "busy" };
  if (loc === "SEARCH_FAILED" || loc === "AMBIGUOUS") {
    return { label: "위치 못 찾음", detail: "이동 화면에서 위치 추정을 다시 해 주세요", tone: "warn" };
  }
  if (ui.locStatus === "not_localized") {
    return { label: "위치 추정 필요", detail: "이동 화면에서 위치 추정을 해 주세요", tone: "warn" };
  }
  if (ui.missionReady === false) {
    return { label: "준비 중", detail: koreanDetail(ui.robotDetail) || "센서 준비 대기", tone: "busy" };
  }

  if (ui.missionPhase === "return") return { label: "목표 복귀", detail: "와드 쪽으로 이동 중", tone: "run" };
  if (ui.missionPhase === "wait") return { label: "목표 대기", detail: "목표 위치에 도착", tone: "run" };
  if (ui.missionPhase === "patrol") return { label: "정찰 중", detail: null, tone: "run" };
  return { label: "대기", detail: koreanDetail(ui.robotDetail), tone: "idle" };
}

function Header({
  variant = "default",
  onAiBack,
  onActivateScreensaver,
  screensaverIdleDisabled = false,
  onToggleScreensaverIdle,
  airPurifierOn = false,
  fragranceOn = false,
  fragranceDiffusing = false,
  arduinoConnected = false,
  onOpenSettings,
  settingsOpen = false,
}) {
  const isAiConsult = variant === "ai-consult";
  const { timeLabel } = useCurrentTime();
  const sttStatus = useSttHealth();
  const wifi = useWifiStatus();
  const [moveUi, setMoveUi] = useState(null);
  const whisperTitle = `위스퍼 ${sttStatus.label} · ${sttStatus.detail}`;
  const deviceRunning = airPurifierOn || fragranceOn || fragranceDiffusing;
  const [screensaverHolding, setScreensaverHolding] = useState(false);
  const longPressTimerRef = useRef(null);
  const suppressClickRef = useRef(false);

  const clearScreensaverLongPress = useCallback(() => {
    if (longPressTimerRef.current != null) {
      window.clearTimeout(longPressTimerRef.current);
      longPressTimerRef.current = null;
    }
  }, []);

  const startScreensaverLongPress = useCallback((event) => {
    if (!onToggleScreensaverIdle) return;
    event.currentTarget.setPointerCapture?.(event.pointerId);
    suppressClickRef.current = false;
    clearScreensaverLongPress();
    setScreensaverHolding(true);
    longPressTimerRef.current = window.setTimeout(() => {
      longPressTimerRef.current = null;
      suppressClickRef.current = true;
      setScreensaverHolding(false);
      onToggleScreensaverIdle();
    }, SCREENSAVER_LONG_PRESS_MS);
  }, [clearScreensaverLongPress, onToggleScreensaverIdle]);

  const finishScreensaverLongPress = useCallback(() => {
    clearScreensaverLongPress();
    setScreensaverHolding(false);
  }, [clearScreensaverLongPress]);

  useEffect(() => {
    if (isAiConsult) return undefined;
    let cancelled = false;

    const load = async () => {
      try {
        const state = await fetchMoveUi();
        if (!cancelled) setMoveUi(state);
      } catch {
        if (!cancelled) setMoveUi(null);
      }
    };

    load();
    const timer = window.setInterval(load, 1000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [isAiConsult]);

  const handleScreensaverClick = useCallback(() => {
    if (suppressClickRef.current) {
      suppressClickRef.current = false;
      return;
    }
    onActivateScreensaver?.();
  }, [onActivateScreensaver]);

  let deviceLabel = "대기";
  let deviceTone = "idle";
  if (airPurifierOn && (fragranceOn || fragranceDiffusing)) {
    deviceLabel = "공기·향기 가동";
    deviceTone = "running";
  } else if (airPurifierOn) {
    deviceLabel = "공기청정 가동";
    deviceTone = "running";
  } else if (fragranceDiffusing) {
    deviceLabel = "발향 중";
    deviceTone = "scent";
  } else if (fragranceOn) {
    deviceLabel = "향기 가동";
    deviceTone = "scent";
  }

  const wifiLevel = wifi.connected ? wifi.level : -1;
  const WifiIcon =
    wifiLevel >= 3
      ? Wifi
      : wifiLevel === 2
        ? WifiHigh
        : wifiLevel === 1
          ? WifiLow
          : wifiLevel === 0
            ? WifiZero
            : WifiOff;
  const wifiLabel = wifi.connected
    ? `${wifi.ssid || "Wi-Fi"} · ${wifi.dbm == null ? "연결됨" : `${wifi.dbm} dBm`}`
    : "Wi-Fi 끊김";
  const robot = describeRobot(moveUi);
  const screensaverLabel = screensaverIdleDisabled
    ? "화면보호기 꺼짐, 길게 눌러 자동 전환 켜기"
    : "화면보호기, 길게 눌러 자동 전환 끄기";

  return (
    <header className={`app-header ${isAiConsult ? "ai-consult" : ""}`}>
      <div
        className={`header-panel header-panel-default ${isAiConsult ? "is-hidden" : ""}`}
      >
        <div className="header-left">
          <div className={`header-icon-box is-${robot.tone}`}>
            <MapPin size={20} strokeWidth={2.4} />
          </div>

          <div className="header-text">
            <h2>{robot.label}</h2>
            {robot.detail ? <p className="header-detail">{robot.detail}</p> : null}
          </div>
        </div>

        <div className="header-right">
          <div
            className={`header-device-chip ${deviceTone}`}
            title={arduinoConnected ? "기기 연결됨" : "기기 연결 대기"}
          >
            {airPurifierOn ? <Wind size={13} strokeWidth={2.6} /> : null}
            {(fragranceOn || fragranceDiffusing) && !airPurifierOn ? (
              <SprayCan size={13} strokeWidth={2.6} />
            ) : null}
            {deviceRunning ? (
              <span className="header-device-dot" aria-hidden="true" />
            ) : (
              <span className="header-device-idle-dot" aria-hidden="true" />
            )}
            <strong>{deviceLabel}</strong>
            {!arduinoConnected && <em>오프라인</em>}
          </div>
          <button
            type="button"
            className="header-refresh-btn"
            onClick={() => window.location.reload()}
            aria-label="새로고침"
            title="새로고침"
          >
            <RefreshCw size={18} strokeWidth={2.4} />
          </button>
          {onActivateScreensaver ? (
            <button
              type="button"
              className={`header-screensaver-btn${screensaverIdleDisabled ? " is-idle-off" : ""}${screensaverHolding ? " is-holding" : ""}`}
              onClick={handleScreensaverClick}
              onPointerDown={startScreensaverLongPress}
              onPointerUp={finishScreensaverLongPress}
              onPointerCancel={finishScreensaverLongPress}
              onContextMenu={(event) => event.preventDefault()}
              aria-label={screensaverLabel}
              title={screensaverLabel}
            >
              {screensaverIdleDisabled ? (
                <EyeOff size={18} strokeWidth={2.4} />
              ) : (
                <Eye size={18} strokeWidth={2.4} />
              )}
            </button>
          ) : null}
          <span
            className={`header-status-icon is-${sttStatus.tone}`}
            title={whisperTitle}
            aria-label={whisperTitle}
          >
            {sttStatus.tone === "off" ? (
              <MicOff size={20} strokeWidth={2.4} />
            ) : (
              <Mic size={20} strokeWidth={2.4} />
            )}
          </span>
          <span
            className={`header-wifi is-${wifi.connected ? `l${wifi.level}` : "off"}`}
            title={wifiLabel}
            aria-label={wifiLabel}
          >
            <WifiIcon size={20} strokeWidth={2.4} />
          </span>
          <span>{timeLabel}</span>
          {onOpenSettings ? (
            <button
              type="button"
              className={`header-settings-btn${settingsOpen ? " is-active" : ""}`}
              onClick={onOpenSettings}
              aria-label={settingsOpen ? "홈으로" : "설정"}
              aria-pressed={settingsOpen}
              title={settingsOpen ? "홈으로" : "설정"}
            >
              <Settings size={18} strokeWidth={2.4} />
            </button>
          ) : null}
        </div>
      </div>

      <div
        className={`header-panel header-panel-ai ${isAiConsult ? "" : "is-hidden"}`}
      >
        <button className="header-ai-back" type="button" onClick={onAiBack}>
          <ArrowLeft size={22} />
          <span>돌아가기</span>
        </button>

        <div className="header-ai-title">
          <Bot size={22} />
          <strong>AI 향기 상담</strong>
        </div>

        <span className="header-ai-live">LIVE</span>
      </div>
    </header>
  );
}

export default Header;
