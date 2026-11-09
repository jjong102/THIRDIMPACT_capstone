import { Check, Eye, Heart, Map, Mic, Moon, Network, Play, PlugZap, Power, RefreshCw, RotateCw, Sparkles, SprayCan, Usb, Volume2, Wifi, WifiOff, Wind } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import AlpacaCharacter from "../components/screensaver/AlpacaCharacter";
import BearCharacter from "../components/screensaver/BearCharacter";
import ElephantCharacter from "../components/screensaver/ElephantCharacter";
import GiraffeCharacter from "../components/screensaver/GiraffeCharacter";
import PandaCharacter from "../components/screensaver/PandaCharacter";
import PigCharacter from "../components/screensaver/PigCharacter";
import {
  SCREENSAVER_CHARACTERS,
  getScreensaverCharacter,
} from "../data/screensaverCharacters";
import { fetchSystemHosts, requestSystemPower } from "../services/systemPower";
import { fetchSttHealth } from "../services/sttTranscribe";
import { fetchSpeakerVolume, fetchVoiceClips, playVoiceClip, setSpeakerVolume, speakText } from "../services/ttsSpeak";
import { setLivingScentDemo } from "../services/mqttAirQuality";
import { AIR_PURIFIER_MODE, AIR_PURIFIER_MODE_LABELS } from "../utils/airPurifierCommand";
import { clampTargetPercent } from "../utils/fragranceIntensity";
import { displayWardScentLabel } from "../utils/scentVisual";
import { sensorLabelTone } from "../utils/sensorLabels";
import {
  clearWardScent,
  getWardScent,
  loadWardScents,
  loadWards,
  wardDisplayName,
} from "../utils/wardStorage";

import MapSettingsPanel from "../components/MapSettingsPanel";
import "./MorePage.css";
import { scentLabel } from "../data/scentRecipes";
import { loadDeveloperMode, saveDeveloperMode } from "../utils/developerMode";

function CharacterPreview({ characterId }) {
  switch (characterId) {
    case "bear":
      return <BearCharacter compact />;
    case "panda":
      return <PandaCharacter compact />;
    case "alpaca":
      return <AlpacaCharacter compact />;
    case "elephant":
      return <ElephantCharacter compact />;
    case "giraffe":
      return <GiraffeCharacter compact />;
    case "pig":
      return <PigCharacter compact />;
    default:
      return <div className="screensaver-preview-empty" />;
  }
}

const POWER_ACTIONS = {
  reboot: {
    id: "reboot",
    title: "다시 시작할까요?",
    description: "기기가 잠시 꺼졌다가 다시 켜집니다.",
    confirmLabel: "재시작",
    pendingLabel: "재시작 중…",
  },
  poweroff: {
    id: "poweroff",
    title: "종료할까요?",
    description: "화면이 꺼집니다. 다시 쓰려면 전원을 켜 주세요.",
    confirmLabel: "종료",
    pendingLabel: "종료 중…",
  },
};

function AirStatusIcon({ id }) {
  if (id === "sleep") {
    return <Moon size={20} strokeWidth={2.2} />;
  }

  if (id === "turbo") {
    return <Heart size={18} strokeWidth={2.2} fill="currentColor" />;
  }

  return <span className="more-air-status-badge">A</span>;
}

const AIR_STATUS_OPTIONS = [
  { id: "auto", label: "자동", hint: AIR_PURIFIER_MODE_LABELS[AIR_PURIFIER_MODE.AUTO], mode: AIR_PURIFIER_MODE.AUTO },
  { id: "sleep", label: "수면", hint: AIR_PURIFIER_MODE_LABELS[AIR_PURIFIER_MODE.SLEEP], mode: AIR_PURIFIER_MODE.SLEEP },
  { id: "turbo", label: "고속", hint: AIR_PURIFIER_MODE_LABELS[AIR_PURIFIER_MODE.TURBO], mode: AIR_PURIFIER_MODE.TURBO },
];

const LIVING_ROOM_KEYS = ["default", "room_1", "room1", "ward_home_1", "ward-home-1"];
const LIVE_LIVING_KEYS = ["live:default", "live:room_1", "live:room1"];

function pickRoomReading(rooms, keys) {
  const map = rooms ?? {};
  for (const key of keys) {
    if (map[key]) return map[key];
  }
  return null;
}

function livingSensorStatus(mqttAirQuality) {
  const rooms = mqttAirQuality?.rooms ?? {};
  const shown =
    pickRoomReading(rooms, LIVING_ROOM_KEYS) ??
    (mqttAirQuality?.label || mqttAirQuality?.raw
      ? {
          label: mqttAirQuality.label,
          confidence: mqttAirQuality.confidence,
          demo: Boolean(mqttAirQuality.demo),
          receivedAt: mqttAirQuality.receivedAt,
        }
      : null);
  const live = pickRoomReading(rooms, LIVE_LIVING_KEYS);
  const mqttConnected = Boolean(mqttAirQuality?.mqttConnected);
  const isDemo = Boolean(shown?.demo || mqttAirQuality?.demo);
  const label = displayWardScentLabel(shown?.label) ?? shown?.label ?? null;
  const confidence =
    typeof shown?.confidence === "number" ? shown.confidence : null;
  const liveLabel = displayWardScentLabel(live?.label) ?? live?.label ?? null;
  const liveConfidence =
    typeof live?.confidence === "number" ? live.confidence : null;

  let sourceLabel = "대기";
  let sourceTone = "idle";
  if (isDemo) {
    sourceLabel = "시연 중";
    sourceTone = "demo";
  } else if (mqttConnected && label) {
    sourceLabel = "실제 감지";
    sourceTone = "live";
  } else if (label) {
    sourceLabel = "마지막 값";
    sourceTone = "idle";
  }

  return {
    mqttConnected,
    isDemo,
    sourceLabel,
    sourceTone,
    label,
    confidence,
    liveLabel,
    liveConfidence,
    error: mqttAirQuality?.error ?? null,
  };
}

const LIVING_DEMO_SCENTS = [
  { id: "fresh", label: "Fresh Air", hint: "공기" },
  { id: "odor", label: "odor", hint: "악취" },
  { id: "orange", label: "Orange", hint: "오렌지" },
  { id: "mint", label: "Peppermint", hint: "페퍼민트" },
];

function describeSttStatus(payload) {
  if (!payload?.ok) {
    return {
      tone: "off",
      label: "미연결",
      detail: "서버에 연결할 수 없어요",
    };
  }
  if (payload.ready) {
    const model = payload.whisperModel ? `Whisper ${payload.whisperModel}` : "모델 준비됨";
    return {
      tone: "on",
      label: "연결됨",
      detail: model,
    };
  }
  return {
    tone: "wait",
    label: "준비 중",
    detail: payload.whisperError || "모델을 불러오는 중…",
  };
}

function bedroomWard() {
  const wards = loadWards();
  return (
    wards.find((ward) => wardDisplayName(ward) === "안방") ||
    wards.find((ward) => {
      const id = String(ward?.id ?? "")
        .toLowerCase()
        .replace(/-/g, "_");
      return id === "room_1" || id === "room1" || id === "ward_home_1";
    }) ||
    wards[0] ||
    null
  );
}

function isBedroomScentUnset() {
  const ward = bedroomWard();
  return Boolean(ward && loadWardScents()?.[ward.id]?.unset);
}

function livingTargetPercent() {
  const wards = loadWards();
  const living =
    wards.find((ward) => wardDisplayName(ward) === "거실") ||
    wards.find((ward) => {
      const id = String(ward?.id ?? "")
        .toLowerCase()
        .replace(/-/g, "_");
      return id === "room_1" || id === "room1" || id === "ward_home_1";
    }) ||
    wards[0];
  return clampTargetPercent(getWardScent(loadWardScents(), living?.id).level);
}

const FRAGRANCE_CHANNELS = [
  { id: "mint", label: "Peppermint", hint: "페퍼민트" },
  { id: "lavender", label: "Lavender", hint: "라벤더" },
  { id: "orange", label: "Orange", hint: "오렌지" },
];

const MORE_TAB_KEY = "air-scent:more-tab";
const MORE_TABS = [
  { id: "device", label: "기기", Icon: Network },
  { id: "control", label: "제어", Icon: Wind },
  { id: "map", label: "맵", Icon: Map },
  { id: "demo", label: "시연", Icon: Sparkles },
  { id: "screen", label: "화면", Icon: Eye },
  { id: "voice", label: "음성", Icon: Volume2 },
];
const MORE_TAB_ALIASES = {
  power: "device",
  hosts: "device",
  air: "control",
  fragrance: "control",
};

function loadMoreTab() {
  try {
    const value = window.sessionStorage.getItem(MORE_TAB_KEY);
    const mapped = MORE_TAB_ALIASES[value] ?? value;
    if (MORE_TABS.some((tab) => tab.id === mapped)) return mapped;
  } catch {
    // ignore
  }
  return "control";
}

function MorePage({
  screensaverCharacterId,
  onSelectScreensaverCharacter,
  airPurifierOn,
  airPurifierMode,
  airPurifierSending = false,
  onToggleAirPurifier,
  onSelectAirPurifierMode,
  onSetAirPurifierStatus,
  fragranceOn = false,
  fragranceChannels = {},
  onToggleFragranceChannel,
  mqttAirQuality = null,
  arduinoConnected = false,
  onReconnectArduino,
  foundingDemo = null,
}) {
  const [arduinoBusy, setArduinoBusy] = useState(false);
  /** 켜면 버튼이 기기를 움직이지 않고 화면 표시만 실제 상태에 맞춘다 (본체 버튼으로 어긋났을 때). */
  const [airSyncOnly, setAirSyncOnly] = useState(false);
  const [speakerVolume, setSpeakerVolumeState] = useState(80);
  const [speakerTestBusy, setSpeakerTestBusy] = useState(false);
  const volumeTimerRef = useRef(null);
  const [activeTab, setActiveTab] = useState(loadMoreTab);
  const [developerMode, setDeveloperMode] = useState(loadDeveloperMode);
  const [pendingAction, setPendingAction] = useState(null);
  const [powerBusy, setPowerBusy] = useState(false);
  const [powerError, setPowerError] = useState("");
  const [hosts, setHosts] = useState([]);
  const [hostsStatus, setHostsStatus] = useState("loading");
  const [sttStatus, setSttStatus] = useState({
    tone: "wait",
    label: "확인 중",
    detail: "음성 인식 서버를 확인하는 중…",
  });
  const [sttBusy, setSttBusy] = useState(false);
  const [demoBusy, setDemoBusy] = useState(false);
  const [bedroomUnset, setBedroomUnset] = useState(isBedroomScentUnset);

  useEffect(() => {
    const sync = () => setBedroomUnset(isBedroomScentUnset());
    window.addEventListener("air-scent-ward-scents-changed", sync);
    return () => window.removeEventListener("air-scent-ward-scents-changed", sync);
  }, []);
  const [voiceBusyId, setVoiceBusyId] = useState(null);
  const [voiceClips, setVoiceClips] = useState([]);
  const [voiceLoading, setVoiceLoading] = useState(false);
  const [voiceLoaded, setVoiceLoaded] = useState(false);
  const [voiceMessage, setVoiceMessage] = useState("");
  const logBodyRef = useRef(null);
  const selectedCharacter = getScreensaverCharacter(screensaverCharacterId);
  const mqttLogs = useMemo(
    () =>
      (mqttAirQuality?.logs ?? []).filter(
        (line) =>
          (line?.kind === "mqtt" || line?.kind === "demo") && line?.text
      ),
    [mqttAirQuality?.logs]
  );

  useEffect(() => {
    const el = logBodyRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [mqttLogs]);

  useEffect(() => {
    if (activeTab !== "control") return;
    let cancelled = false;
    fetchSpeakerVolume()
      .then((payload) => {
        if (!cancelled && typeof payload.volume === "number") {
          setSpeakerVolumeState(payload.volume);
        }
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [activeTab]);

  useEffect(() => {
    if (activeTab !== "device") return;

    let cancelled = false;

    const loadStt = async () => {
      try {
        const payload = await fetchSttHealth();
        if (cancelled) return;
        setSttStatus(describeSttStatus(payload));
      } catch {
        if (cancelled) return;
        setSttStatus(describeSttStatus(null));
      }
    };

    loadStt();
    const sttTimer = window.setInterval(loadStt, 5000);

    const loadHosts = async () => {
      try {
        const next = await fetchSystemHosts();
        if (cancelled) return;
        setHosts(next);
        setHostsStatus("ok");
      } catch {
        if (cancelled) return;
        setHosts([]);
        setHostsStatus("error");
      }
    };

    loadHosts();
    const timer = window.setInterval(loadHosts, 15000);
    return () => {
      cancelled = true;
      window.clearInterval(sttTimer);
      window.clearInterval(timer);
    };
  }, [activeTab]);

  const refreshStt = async () => {
    if (sttBusy) return;
    setSttBusy(true);
    try {
      const payload = await fetchSttHealth();
      setSttStatus(describeSttStatus(payload));
    } catch {
      setSttStatus(describeSttStatus(null));
    } finally {
      setSttBusy(false);
    }
  };

  const livingStatus = useMemo(
    () => livingSensorStatus(mqttAirQuality),
    [mqttAirQuality]
  );

  const livingDemoId = useMemo(() => {
    const label = displayWardScentLabel(mqttAirQuality?.label) ?? mqttAirQuality?.label ?? "";
    const tone = sensorLabelTone(label);
    if (tone === "lavender") return "odor";
    return tone;
  }, [mqttAirQuality]);

  /** demoId: fresh · odor · orange · mint — 센서 원래 라벨로는 서버가 바꾼다. */
  const applyLivingDemo = async (demoId) => {
    if (demoBusy) return;
    setDemoBusy(true);
    try {
      await setLivingScentDemo(
        demoId,
        demoId ? livingTargetPercent() : null
      );
    } catch {
      // 다음 폴링에서 실제 값이 보임
    } finally {
      setDemoBusy(false);
    }
  };

  const pending = pendingAction ? POWER_ACTIONS[pendingAction] : null;

  const selectTab = (tabId) => {
    setActiveTab(tabId);
    try {
      window.sessionStorage.setItem(MORE_TAB_KEY, tabId);
    } catch {
      // ignore
    }
  };

  const openPowerDialog = (action) => {
    setPowerError("");
    setPendingAction(action);
  };

  const closePowerDialog = () => {
    if (powerBusy) return;
    setPendingAction(null);
    setPowerError("");
  };

  const changeSpeakerVolume = (next) => {
    const volume = Math.max(0, Math.min(100, Number(next) || 0));
    setSpeakerVolumeState(volume);
    if (volumeTimerRef.current) {
      window.clearTimeout(volumeTimerRef.current);
    }
    volumeTimerRef.current = window.setTimeout(() => {
      setSpeakerVolume(volume)
        .then((payload) => {
          if (typeof payload.volume === "number") {
            setSpeakerVolumeState(payload.volume);
          }
        })
        .catch(() => {});
    }, 120);
  };

  const testSpeaker = async () => {
    if (speakerTestBusy) return;
    setSpeakerTestBusy(true);
    try {
      await speakText("스피커 테스트입니다.");
    } finally {
      window.setTimeout(() => setSpeakerTestBusy(false), 800);
    }
  };

  const reconnectArduino = async () => {
    if (arduinoBusy) return;
    setArduinoBusy(true);
    try {
      await onReconnectArduino?.();
    } finally {
      setArduinoBusy(false);
    }
  };

  const loadVoiceClips = async () => {
    if (voiceLoading) return;
    setVoiceLoading(true);
    setVoiceMessage("");
    try {
      const clips = await fetchVoiceClips();
      setVoiceClips(clips);
      setVoiceLoaded(true);
      if (clips.length === 0) {
        setVoiceMessage("폴더에 녹음 파일이 없습니다.");
      }
    } catch (error) {
      setVoiceClips([]);
      setVoiceLoaded(true);
      setVoiceMessage(error instanceof Error ? error.message : "녹음 목록을 읽지 못했습니다");
    } finally {
      setVoiceLoading(false);
    }
  };

  const playVoice = async (clip) => {
    if (voiceBusyId) return;
    setVoiceBusyId(clip.file);
    setVoiceMessage("");
    try {
      const result = await playVoiceClip(clip.file);
      if (!result?.ok) {
        setVoiceMessage(result?.error || "재생하지 못했습니다");
      }
    } finally {
      setVoiceBusyId(null);
    }
  };

  const confirmPowerAction = async () => {
    if (!pendingAction || powerBusy) return;

    setPowerBusy(true);
    setPowerError("");

    try {
      await requestSystemPower(pendingAction);
    } catch (error) {
      setPowerBusy(false);
      setPowerError(
        error instanceof Error ? error.message : "요청에 실패했습니다",
      );
    }
  };

  return (
    <section className="more-page">
      <nav className="more-tabs" aria-label="더보기 메뉴">
        {MORE_TABS.map((tab) => {
          const Icon = tab.Icon;
          const selected = activeTab === tab.id;
          return (
            <button
              key={tab.id}
              type="button"
              className={`more-tab${selected ? " selected" : ""}`}
              aria-pressed={selected}
              onClick={() => selectTab(tab.id)}
            >
              <Icon size={18} strokeWidth={2.4} />
              <span>{tab.label}</span>
            </button>
          );
        })}
      </nav>

      <div className="more-tab-panel">
        {activeTab === "device" ? (
          <section className="more-air-status-card more-tab-card more-device-card">
            <button
              type="button"
              className={`more-dev-mode${developerMode ? " active" : ""}`}
              aria-pressed={developerMode}
              onClick={() => {
                const next = !developerMode;
                setDeveloperMode(next);
                saveDeveloperMode(next);
              }}
            >
              <span>개발자 모드</span>
              <strong>{developerMode ? "켜짐" : "꺼짐"}</strong>
            </button>
            <article className={`more-arduino-card ${arduinoConnected ? "is-on" : "is-off"}`}>
              <div className="more-arduino-copy">
                <div className="more-arduino-icon" aria-hidden="true">
                  <Usb size={20} strokeWidth={2.3} />
                </div>
                <div>
                  <p className="more-section-label">아두이노</p>
                  <strong>{arduinoConnected ? "연결됨" : "미연결"}</strong>
                  <span>
                    {arduinoBusy
                      ? "다시 연결하는 중…"
                      : arduinoConnected
                        ? "향 · 공청 컨트롤러"
                        : "끊기면 자동으로 다시 붙습니다"}
                  </span>
                </div>
              </div>
              <button
                type="button"
                className="more-arduino-btn"
                onClick={() => void reconnectArduino()}
                disabled={arduinoBusy}
              >
                <PlugZap size={18} strokeWidth={2.4} />
                <span>{arduinoBusy ? "연결 중" : "다시 연결"}</span>
              </button>
            </article>
            <article className={`more-arduino-card is-${sttStatus.tone}`}>
              <div className="more-arduino-copy">
                <div className="more-arduino-icon" aria-hidden="true">
                  <Mic size={20} strokeWidth={2.3} />
                </div>
                <div>
                  <p className="more-section-label">음성 인식</p>
                  <strong>{sttStatus.label}</strong>
                  <span>{sttBusy ? "다시 확인하는 중…" : sttStatus.detail}</span>
                </div>
              </div>
              <button
                type="button"
                className="more-arduino-btn"
                onClick={() => void refreshStt()}
                disabled={sttBusy}
              >
                <RefreshCw size={18} strokeWidth={2.4} />
                <span>{sttBusy ? "확인 중" : "다시 확인"}</span>
              </button>
            </article>
            {hostsStatus === "loading" ? (
              <p className="more-ip-empty">기기 정보를 읽는 중…</p>
            ) : hostsStatus === "error" ? (
              <p className="more-ip-empty is-error">기기 정보를 읽지 못했습니다</p>
            ) : (
              <div className="more-host-grid">
                {hosts.map((host) => (
                  <article
                    key={host.id}
                    className={`more-host-item ${host.online ? "online" : "offline"}`}
                  >
                    <div className="more-host-item-head">
                      <div className="more-host-item-title">
                        <strong>{host.role}</strong>
                        {host.duty ? <span>{host.duty}</span> : null}
                      </div>
                      <span className={`more-host-chip ${host.online ? "ok" : "off"}`}>
                        {host.online ? "연결됨" : "끊김"}
                      </span>
                    </div>
                    <dl className="more-host-dl">
                      <div className="more-host-row">
                        <dt>whoami</dt>
                        <dd>{host.user || "확인 불가"}</dd>
                      </div>
                      <div className="more-host-row">
                        <dt>hostname</dt>
                        <dd>{host.hostname || "확인 불가"}</dd>
                      </div>
                      <div className="more-host-row">
                        <dt>IP</dt>
                        <dd>
                          {(host.addresses ?? []).length ? (
                            <ul className="more-ip-list">
                              {host.addresses.map((ip) => (
                                <li key={ip} className="more-ip-item">
                                  {ip}
                                </li>
                              ))}
                            </ul>
                          ) : (
                            host.hint || "10.96 주소 없음"
                          )}
                        </dd>
                      </div>
                    </dl>
                    {host.hint && (host.addresses ?? []).length ? (
                      <p className="more-host-hint">{host.hint}</p>
                    ) : null}
                  </article>
                ))}
              </div>
            )}
            <div className="more-power-row">
              <button
                type="button"
                className="more-power-button reload"
                onClick={() => window.location.reload()}
              >
                <RefreshCw size={20} strokeWidth={2.4} />
                <span>재로드</span>
              </button>
              <button
                type="button"
                className="more-power-button reboot"
                onClick={() => openPowerDialog("reboot")}
              >
                <RotateCw size={20} strokeWidth={2.4} />
                <span>재시작</span>
              </button>
              <button
                type="button"
                className="more-power-button poweroff"
                onClick={() => openPowerDialog("poweroff")}
              >
                <Power size={20} strokeWidth={2.4} />
                <span>종료</span>
              </button>
            </div>
          </section>
        ) : null}

        {activeTab === "control" ? (
          <section className="more-air-status-card more-tab-card more-control-card">
            <div className="more-control-block">
              <div className="more-air-control-head">
                <p className="more-section-label">
                  공청기{airPurifierOn ? "" : " · 꺼짐"}
                </p>
                <button
                  type="button"
                  className={`more-air-sync-toggle${airSyncOnly ? " active" : ""}`}
                  aria-pressed={airSyncOnly}
                  onClick={() => setAirSyncOnly((value) => !value)}
                >
                  {airSyncOnly ? "표시만 맞추는 중" : "표시만 맞추기"}
                </button>
              </div>
              <div className="more-air-status-grid more-air-control-grid" role="group" aria-label="공청기 제어">
                <button
                  type="button"
                  className={`more-air-status-btn mode-off${airPurifierOn ? "" : " selected"}`}
                  aria-pressed={!airPurifierOn}
                  disabled={!airSyncOnly && airPurifierSending}
                  onClick={() => {
                    if (airSyncOnly) {
                      onSetAirPurifierStatus?.(false, airPurifierMode);
                    } else if (airPurifierOn) {
                      onToggleAirPurifier?.();
                    }
                  }}
                >
                  <span className="more-air-status-btn-icon" aria-hidden="true">
                    <Power size={20} strokeWidth={2.2} />
                  </span>
                  <strong>끄기</strong>
                  <span>{airPurifierSending ? "전송 중..." : "전원 OFF"}</span>
                </button>
                {AIR_STATUS_OPTIONS.map((option) => {
                  const selected = airPurifierOn && airPurifierMode === option.mode;

                  return (
                    <button
                      key={option.id}
                      type="button"
                      className={`more-air-status-btn mode-${option.id}${selected ? " selected" : ""}`}
                      aria-pressed={selected}
                      disabled={!airSyncOnly && airPurifierSending}
                      onClick={() => {
                        if (airSyncOnly) {
                          onSetAirPurifierStatus?.(true, option.mode);
                        } else if (!selected) {
                          onSelectAirPurifierMode?.(option.mode);
                        }
                      }}
                    >
                      <span className="more-air-status-btn-icon" aria-hidden="true">
                        <AirStatusIcon id={option.id} />
                      </span>
                      <strong>{option.label}</strong>
                      <span>{option.hint}</span>
                    </button>
                  );
                })}
              </div>
            </div>
            <div className="more-control-block">
              <p className="more-section-label">
                향기{fragranceOn ? "" : " · 꺼짐"}
              </p>
              <div className="more-air-status-grid" role="group" aria-label="향기 개별 전원">
                {FRAGRANCE_CHANNELS.map((channel) => {
                  const selected = Boolean(fragranceOn && fragranceChannels[channel.id]);

                  return (
                    <button
                      key={channel.id}
                      type="button"
                      className={`more-air-status-btn more-fragrance-btn channel-${channel.id}${selected ? " selected" : ""}`}
                      aria-pressed={selected}
                      onClick={() => onToggleFragranceChannel?.(channel.id)}
                    >
                      <span className="more-air-status-btn-icon" aria-hidden="true">
                        <span className="more-fragrance-dot" />
                      </span>
                      <strong>{channel.label}</strong>
                      <span>{selected ? "켜짐" : "꺼짐"}</span>
                    </button>
                  );
                })}
              </div>
            </div>
            <div className="more-control-block">
              <article className="more-volume-card">
                <div className="more-volume-head">
                  <div className="more-volume-copy">
                    <div className="more-arduino-icon" aria-hidden="true">
                      <Volume2 size={20} strokeWidth={2.3} />
                    </div>
                    <div>
                      <p className="more-section-label">스피커</p>
                      <strong>{speakerVolume}%</strong>
                      <span>UACDemo 출력</span>
                    </div>
                  </div>
                  <button
                    type="button"
                    className="more-arduino-btn"
                    onClick={() => void testSpeaker()}
                    disabled={speakerTestBusy}
                  >
                    <Play size={16} strokeWidth={2.6} />
                    <span>{speakerTestBusy ? "재생 중" : "테스트"}</span>
                  </button>
                </div>
                <input
                  className="more-volume-range"
                  type="range"
                  min="0"
                  max="100"
                  step="5"
                  value={speakerVolume}
                  style={{ "--volume-progress": `${speakerVolume}%` }}
                  onChange={(event) => changeSpeakerVolume(event.target.value)}
                  aria-label="스피커 음량"
                />
              </article>
            </div>
          </section>
        ) : null}

        {activeTab === "map" ? <MapSettingsPanel /> : null}

        {activeTab === "demo" ? (
          <section className="more-air-status-card more-demo-card more-tab-card">
            <header className="more-air-status-header">
              <div className="more-demo-icon" aria-hidden="true">
                <Sparkles size={20} strokeWidth={2.4} />
              </div>
              <div className="more-air-status-copy">
                <p className="more-air-status-label">시연</p>
                <h1>거실 향 감지</h1>
                <p className="more-air-status-desc">
                  센서에서 받아오는 값을 그대로 보여 줍니다.
                </p>
              </div>
            </header>

            <div
              className={`more-demo-status ${livingStatus.mqttConnected ? "is-online" : "is-offline"}`}
            >
              <span
                className={`more-demo-chip ${livingStatus.mqttConnected ? "ok" : "off"}`}
              >
                {livingStatus.mqttConnected ? (
                  <Wifi size={13} strokeWidth={2.6} />
                ) : (
                  <WifiOff size={13} strokeWidth={2.6} />
                )}
                {livingStatus.mqttConnected ? "센서 연결됨" : "센서 미연결"}
              </span>
              <span className={`more-demo-chip ${livingStatus.sourceTone}`}>
                {livingStatus.sourceLabel}
              </span>
              <strong>
                {livingStatus.label
                  ? `${scentLabel(livingStatus.label)}${
                      livingStatus.confidence != null
                        ? ` ${Math.round(livingStatus.confidence)}%`
                        : ""
                    }`
                  : "값 없음"}
              </strong>
            </div>
            {livingStatus.isDemo && livingStatus.liveLabel ? (
              <p className="more-demo-live-line">
                실제 센서 {livingStatus.liveLabel}
                {livingStatus.liveConfidence != null
                  ? ` ${Math.round(livingStatus.liveConfidence)}%`
                  : ""}
              </p>
            ) : livingStatus.error && !livingStatus.mqttConnected ? (
              <p className="more-demo-live-line is-error">{livingStatus.error}</p>
            ) : null}

            <div className="more-demo-log" aria-label="MQTT 수신 로그">
              <div className="more-demo-log-head">수신 로그 · sensor/air_quality</div>
              <div className="more-demo-log-body" ref={logBodyRef}>
                {mqttLogs.length === 0 ? (
                  <p className="more-demo-log-line kind-sys">
                    <span>센서 수신 대기 중…</span>
                  </p>
                ) : (
                  mqttLogs.map((line) => (
                    <p
                      key={line.id ?? `${line.time}-${line.text}`}
                      className={`more-demo-log-line kind-${line.kind ?? "mqtt"}`}
                    >
                      <time>{line.time}</time>
                      <span>{line.text}</span>
                    </p>
                  ))
                )}
              </div>
            </div>

            <div className="more-demo-grid" role="group" aria-label="거실 향 감지 시연">
              {LIVING_DEMO_SCENTS.map((option) => {
                const selected = livingStatus.isDemo && livingDemoId === option.id;
                return (
                  <button
                    key={option.id}
                    type="button"
                    className={`more-air-status-btn more-demo-btn scent-${option.id}${selected ? " selected" : ""}`}
                    aria-pressed={selected}
                    disabled={demoBusy}
                    onClick={() => applyLivingDemo(option.id)}
                  >
                    <strong>{option.label}</strong>
                    <span>{option.hint}</span>
                  </button>
                );
              })}
            </div>
            <button
              type="button"
              className={`more-demo-clear${livingStatus.isDemo ? " is-active" : ""}`}
              disabled={demoBusy}
              onClick={() => applyLivingDemo(null)}
            >
              {demoBusy ? "처리 중…" : "시연 취소"}
            </button>
            <button
              type="button"
              className={`more-demo-clear${bedroomUnset ? " is-active" : ""}`}
              disabled={bedroomUnset}
              onClick={() => {
                const ward = bedroomWard();
                if (!ward?.id) return;
                clearWardScent(ward.id);
                setBedroomUnset(true);
              }}
            >
              {bedroomUnset ? "안방 향기 미설정됨" : "안방 향기 미설정"}
            </button>
            {foundingDemo ? (
              <button
                type="button"
                className={`more-founding-btn${foundingDemo.armed ? " is-active" : ""}`}
                aria-pressed={foundingDemo.armed}
                onClick={foundingDemo.toggle}
              >
                <strong>창설시연</strong>
                <span>
                  {!foundingDemo.armed
                    ? "꺼짐 · 눌러서 시작"
                    : foundingDemo.phase === "purifying"
                      ? "공기청정 10초"
                      : foundingDemo.phase === "spraying"
                        ? `${foundingDemo.sprayLabel ?? ""} 발향 중`
                        : foundingDemo.phase === "waiting"
                          ? "완료 · air 대기"
                          : `scent 20% 이상 대기 (${foundingDemo.percent ?? "-"}%)`}
                </span>
              </button>
            ) : null}
          </section>
        ) : null}

        {activeTab === "voice" ? (
          <section className="more-air-status-card more-tab-card more-voice-card">
            <header className="more-air-status-header">
              <div className="more-demo-icon" aria-hidden="true">
                <Volume2 size={20} strokeWidth={2.4} />
              </div>
              <div className="more-air-status-copy">
                <p className="more-air-status-label">음성</p>
                <h1>녹음 재생</h1>
                <p className="more-air-status-desc">
                  public/voice-clips 에 넣은 녹음이 스피커로 나갑니다.
                </p>
              </div>
            </header>
            <button
              type="button"
              className="more-voice-load"
              onClick={() => void loadVoiceClips()}
              disabled={voiceLoading}
            >
              <RefreshCw size={18} strokeWidth={2.6} />
              <span>{voiceLoading ? "불러오는 중" : "로드하기"}</span>
            </button>
            {voiceLoaded ? (
              <div className="more-voice-list" role="group" aria-label="녹음 음성">
                {voiceClips.map((clip) => {
                  const busy = voiceBusyId === clip.file;
                  return (
                    <button
                      key={clip.file}
                      type="button"
                      className={`more-voice-btn${busy ? " is-busy" : ""}`}
                      disabled={voiceBusyId !== null}
                      onClick={() => void playVoice(clip)}
                    >
                      <Play size={18} strokeWidth={2.6} />
                      <strong>{clip.label.replaceAll("_", " ")}</strong>
                    </button>
                  );
                })}
              </div>
            ) : null}
            {voiceMessage ? (
              <p className="more-voice-message is-error">{voiceMessage}</p>
            ) : voiceLoaded ? null : (
              <p className="more-voice-message">
                녹음 파일을 폴더에 넣고 로드하기를 누르세요.
              </p>
            )}
          </section>
        ) : null}

        {activeTab === "screen" ? (
          <section className="more-air-status-card more-tab-card more-screen-card">
            <header className="more-air-status-header">
              <div className="screensaver-settings-icon" aria-hidden="true">
                <Eye size={20} strokeWidth={2.4} />
              </div>
              <div className="more-air-status-copy">
                <p className="more-air-status-label">화면보호기</p>
                <h1>캐릭터 설정</h1>
                <p className="more-air-status-desc">
                  {selectedCharacter?.name ?? "캐릭터"} · 눌러서 바꾸기
                </p>
              </div>
              <div className="screensaver-settings-current" aria-hidden="true">
                <CharacterPreview characterId={screensaverCharacterId} />
              </div>
            </header>

            <div className="screensaver-character-grid">
              {SCREENSAVER_CHARACTERS.map((character) => {
                const selected = character.id === screensaverCharacterId;
                const disabled = !character.available;

                return (
                  <button
                    key={character.id}
                    type="button"
                    className={`screensaver-character-card${selected ? " selected" : ""}${disabled ? " disabled" : ""}`}
                    disabled={disabled}
                    onClick={() => onSelectScreensaverCharacter?.(character.id)}
                  >
                    <div className="screensaver-character-preview">
                      <CharacterPreview characterId={character.id} />
                    </div>

                    <div className="screensaver-character-meta">
                      <strong>{character.name}</strong>
                      <span>{character.description}</span>
                    </div>

                    {selected ? (
                      <span className="screensaver-character-check" aria-hidden="true">
                        <Check size={16} strokeWidth={2.8} />
                      </span>
                    ) : null}
                  </button>
                );
              })}
            </div>
          </section>
        ) : null}
      </div>

      {pending ? (
        <div className="more-power-overlay" role="presentation">
          <div
            className="more-power-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="more-power-title"
          >
            <h2 id="more-power-title">{pending.title}</h2>
            <p>{pending.description}</p>
            {powerError ? (
              <p className="more-power-error">{powerError}</p>
            ) : null}
            <div className="more-power-dialog-actions">
              <button
                type="button"
                className="more-power-dialog-cancel"
                onClick={closePowerDialog}
                disabled={powerBusy}
              >
                취소
              </button>
              <button
                type="button"
                className={`more-power-dialog-confirm ${pendingAction}`}
                onClick={confirmPowerAction}
                disabled={powerBusy}
              >
                {powerBusy ? pending.pendingLabel : pending.confirmLabel}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </section>
  );
}

export default MorePage;
