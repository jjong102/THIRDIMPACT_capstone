import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowLeft, Bot, Eye, EyeOff, MapPin, Mic, MicOff, RefreshCw, Settings, Wifi, WifiHigh, WifiLow, WifiOff, WifiZero, Wind, SprayCan } from "lucide-react";

import { useCurrentTime } from "../hooks/useCurrentTime";
import useGdmRobotPose from "../hooks/useGdmRobotPose";
import { useSttHealth } from "../hooks/useSttHealth";
import { useWifiStatus } from "../hooks/useWifiStatus";
import { fetchMoveUi } from "../services/moveUi";
import "./Header.css";

const SCREENSAVER_LONG_PRESS_MS = 650;

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
  const { pose } = useGdmRobotPose(!isAiConsult);
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
  const locating = ["VERIFYING", "SEARCHING", "LOCAL_RECHECK"].includes(moveUi?.localizationState);
  let robotLabel = "대기";
  if (moveUi?.poseEstimating || locating) robotLabel = "위치 추정 중";
  else if (moveUi?.robotMode === "teach" || moveUi?.missionPhase === "setup") robotLabel = "목표 설정";
  else if (moveUi?.manualControl || moveUi?.missionPhase === "manual") robotLabel = "수동";
  else if (moveUi?.arrivalSequence?.phase === "purify") robotLabel = "청정 중";
  else if (moveUi?.arrivalSequence?.phase === "spray") robotLabel = "발향 중";
  else if (moveUi?.missionPhase === "return") robotLabel = "목표 복귀";
  else if (moveUi?.missionPhase === "patrol") robotLabel = "정찰 중";
  else if (moveUi?.missionPhase === "wait") robotLabel = "목표 대기";
  else if (moveUi?.locStatus === "not_localized" || moveUi?.missionReady === false) {
    robotLabel = "준비 안 됨";
  }
  const locationLabel = pose?.live ? robotLabel : "위치 없음";
  const screensaverLabel = screensaverIdleDisabled
    ? "화면보호기 꺼짐, 길게 눌러 자동 전환 켜기"
    : "화면보호기, 길게 눌러 자동 전환 끄기";

  return (
    <header className={`app-header ${isAiConsult ? "ai-consult" : ""}`}>
      <div
        className={`header-panel header-panel-default ${isAiConsult ? "is-hidden" : ""}`}
      >
        <div className="header-left">
          <div className="header-icon-box">
            <MapPin size={20} strokeWidth={2.4} />
          </div>

          <div className="header-text">
            {locationLabel === "위치 없음" ? <p>{robotLabel}</p> : null}
            <h2>{locationLabel}</h2>
            {moveUi?.robotDetail ? <p className="header-detail">{moveUi.robotDetail}</p> : null}
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
