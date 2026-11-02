import { useState, useMemo, useCallback, useEffect, useRef } from "react";

import Header from "./components/Header";
import EyeScreensaver from "./components/EyeScreensaver";

import ControlPage from "./pages/ControlPage";
import MovePage from "./pages/MovePage";
import MorePage from "./pages/MorePage";

import useArduinoControllers from "./hooks/useArduinoControllers";
import useMqttAirQuality from "./hooks/useMqttAirQuality";
import useScentMission from "./hooks/useScentMission";
import useScentMissionNav from "./hooks/useScentMissionNav";
import useOdorResponse from "./hooks/useOdorResponse";
import useFoundingDemo from "./hooks/useFoundingDemo";
import { persistWardScent } from "./utils/wardStorage";
import { clampTargetPercent, resolveMistScale } from "./utils/fragranceIntensity";
import { mockRobotData } from "./data/mockData";
import { scentDisplayName } from "./data/scentRecipes";
import {
  loadInitialFragranceState,
  saveFragranceSession,
} from "./utils/fragranceStorage";
import {
  loadScreensaverCharacterId,
  saveScreensaverCharacterId,
  loadScreensaverIdleDisabled,
  saveScreensaverIdleDisabled,
} from "./utils/screensaverStorage";
import {
  loadAirPurifierStatus,
  saveAirPurifierStatus,
} from "./utils/airPurifierStorage";

const SCREENSAVER_IDLE_MS = 45000;

function App() {
  const [activePage, setActivePage] = useState("home");
  const [hideBottomNav, setHideBottomNav] = useState(false);
  const [aiBackHandler, setAiBackHandler] = useState(null);
  const [screensaverActive, setScreensaverActive] = useState(false);
  const [screensaverCharacterId, setScreensaverCharacterId] = useState(
    loadScreensaverCharacterId
  );
  const [screensaverIdleDisabled, setScreensaverIdleDisabled] = useState(
    loadScreensaverIdleDisabled
  );
  const idleTimerRef = useRef(null);
  const screensaverActiveRef = useRef(false);
  const screensaverIdleDisabledRef = useRef(screensaverIdleDisabled);

  const handleAiSessionChange = useCallback((session) => {
    setHideBottomNav(Boolean(session?.active));
    setAiBackHandler(() => session?.onBack ?? null);
  }, []);

  const bumpActivity = useCallback(() => {
    if (screensaverActiveRef.current) return;
    if (idleTimerRef.current) {
      window.clearTimeout(idleTimerRef.current);
      idleTimerRef.current = null;
    }
    if (screensaverIdleDisabledRef.current) return;
    idleTimerRef.current = window.setTimeout(() => {
      screensaverActiveRef.current = true;
      setScreensaverActive(true);
    }, SCREENSAVER_IDLE_MS);
  }, []);

  const toggleScreensaverIdle = useCallback(() => {
    const next = !screensaverIdleDisabledRef.current;
    screensaverIdleDisabledRef.current = next;
    setScreensaverIdleDisabled(next);
    saveScreensaverIdleDisabled(next);
    if (next) {
      if (idleTimerRef.current) {
        window.clearTimeout(idleTimerRef.current);
        idleTimerRef.current = null;
      }
      return;
    }
    bumpActivity();
  }, [bumpActivity]);

  const dismissScreensaver = useCallback(() => {
    screensaverActiveRef.current = false;
    setScreensaverActive(false);
    bumpActivity();
  }, [bumpActivity]);

  const activateScreensaver = useCallback(() => {
    if (idleTimerRef.current) {
      window.clearTimeout(idleTimerRef.current);
    }
    screensaverActiveRef.current = true;
    setScreensaverActive(true);
  }, []);

  const handleSelectScreensaverCharacter = useCallback((id) => {
    setScreensaverCharacterId(id);
    saveScreensaverCharacterId(id);
  }, []);

  useEffect(() => {
    bumpActivity();

    const onActivity = () => {
      bumpActivity();
    };

    const events = ["pointerdown", "keydown", "touchstart", "wheel", "scroll"];
    events.forEach((eventName) => {
      window.addEventListener(eventName, onActivity, { passive: true });
    });

    return () => {
      if (idleTimerRef.current) window.clearTimeout(idleTimerRef.current);
      events.forEach((eventName) => {
        window.removeEventListener(eventName, onActivity);
      });
    };
  }, [bumpActivity]);

  const savedAirPurifier = loadAirPurifierStatus();
  const savedFragrance = loadInitialFragranceState();
  const [airPurifierOn, setAirPurifierOn] = useState(savedAirPurifier.on);
  const [airPurifierMode, setAirPurifierMode] = useState(savedAirPurifier.mode);
  const [fragranceOn, setFragranceOn] = useState(savedFragrance.fragranceOn);
  const [fragranceChannels, setFragranceChannels] = useState(
    savedFragrance.fragranceChannels
  );

  const [currentFragrance, setCurrentFragrance] = useState(
    savedFragrance.currentFragrance
  );

  const [fragranceLevel, setFragranceLevel] = useState(
    () => savedFragrance.fragranceLevel
  );

  const [fragranceBlend, setFragranceBlend] = useState(
    savedFragrance.fragranceBlend
  );

  const [fragranceDiffusing, setFragranceDiffusing] = useState(
    savedFragrance.fragranceDiffusing
  );
  const [fragranceDispenseComplete, setFragranceDispenseComplete] =
    useState(savedFragrance.fragranceDispenseComplete);

  const [recommendedFragrance, setRecommendedFragrance] = useState(
    savedFragrance.recommendedFragrance
  );

  const [recommendedReason, setRecommendedReason] = useState(
    savedFragrance.recommendedReason
  );
  const [lastDispenseAt, setLastDispenseAt] = useState(
    savedFragrance.lastDispenseAt
  );

  const mqttAirQuality = useMqttAirQuality();
  const { goToWard, mapMeta } = useScentMissionNav();
  useOdorResponse({
    mqttAirQuality,
    goToWard,
    ready: Boolean(mapMeta),
  });
  const scentMission = useScentMission({
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
  });

  const mistScale = useMemo(
    () =>
      resolveMistScale({
        fragranceLevel,
        currentFragrance,
        fragranceBlend,
        mqttAirQuality,
        wardId: scentMission.wardId,
      }),
    [
      fragranceLevel,
      currentFragrance,
      fragranceBlend,
      mqttAirQuality,
      scentMission.wardId,
    ]
  );

  const {
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
  } = useArduinoControllers({
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
    mistScale,
  });

  const foundingDemo = useFoundingDemo({
    mqttAirQuality,
    sendControlCommands,
    setExternalControl,
  });

  useEffect(() => {
    saveAirPurifierStatus({
      on: airPurifierOn,
      mode: airPurifierMode,
    });
  }, [airPurifierOn, airPurifierMode]);

  useEffect(() => {
    saveFragranceSession({
      currentFragrance,
      recommendedFragrance,
      recommendedReason,
      fragranceLevel,
      fragranceBlend,
      fragranceChannels,
      fragranceOn,
      fragranceDiffusing,
      fragranceDispenseComplete,
      lastDispenseAt,
    });
  }, [
    currentFragrance,
    recommendedFragrance,
    recommendedReason,
    fragranceLevel,
    fragranceBlend,
    fragranceChannels,
    fragranceOn,
    fragranceDiffusing,
    fragranceDispenseComplete,
    lastDispenseAt,
  ]);

  const handleApplyFragrance = useCallback((payload) => {
    const fragrance =
      typeof payload === "string" ? payload : payload?.fragrance;
    const persistOnly = Boolean(
      typeof payload === "object" && payload?.persistOnly
    );

    if (typeof payload === "object" && payload !== null) {
      if (payload.wardId) {
        const patch = {};
        if (fragrance) patch.fragrance = scentDisplayName(fragrance);
        if (payload.blend) patch.blend = payload.blend;
        if (payload.intensity) patch.level = clampTargetPercent(payload.intensity);
        if (Object.keys(patch).length > 0) {
          persistWardScent(payload.wardId, patch);
        }
      }

      if (payload.blend) {
        setFragranceBlend(payload.blend);
        setFragranceChannels({
          mint: Number(payload.blend.mint) > 0,
          lavender: Number(payload.blend.lavender) > 0,
          orange: Number(payload.blend.orange) > 0,
        });
      }
      if (payload.intensity) {
        setFragranceLevel(clampTargetPercent(payload.intensity));
      }
      if (payload.reason) {
        setRecommendedReason(payload.reason);
      }
    }

    setCurrentFragrance(scentDisplayName(fragrance));
    setRecommendedFragrance(scentDisplayName(fragrance));
    setFragranceDispenseComplete(false);
    if (!(typeof payload === "object" && payload?.blend)) {
      setFragranceChannels({ mint: true, lavender: true, orange: true });
    }

    saveFragranceSession({
      currentFragrance: scentDisplayName(fragrance),
      recommendedFragrance: scentDisplayName(fragrance),
      recommendedReason:
        typeof payload === "object" ? payload?.reason ?? undefined : undefined,
      fragranceLevel:
        typeof payload === "object" && payload?.intensity
          ? clampTargetPercent(payload.intensity)
          : undefined,
      fragranceBlend:
        typeof payload === "object" ? payload?.blend ?? undefined : undefined,
      fragranceChannels:
        typeof payload === "object" && payload?.blend
          ? {
              mint: Number(payload.blend.mint) > 0,
              lavender: Number(payload.blend.lavender) > 0,
              orange: Number(payload.blend.orange) > 0,
            }
          : { mint: true, lavender: true, orange: true },
      fragranceDispenseComplete: false,
    });

    if (!persistOnly) {
      setActivePage("home");
    }
  }, []);

  useEffect(() => {
    if (!fragranceOn && !fragranceDispenseComplete) {
      setFragranceDiffusing(false);
    }
  }, [fragranceOn, fragranceDispenseComplete]);

  const handleStopFragrance = useCallback(() => {
    scentMission.abort();
    stopFragrance();
  }, [scentMission.abort, stopFragrance]);

  const robotData = useMemo(
    () => ({
      ...mockRobotData,
      currentFragrance,
      fragranceLevel,
      arduinoConnected,
    }),
    [currentFragrance, fragranceLevel, arduinoConnected]
  );

  // 여기에 ROS 데이터 구독 연결 예정
  // 예: rosbridge WebSocket / ROS2 topic subscribe / Arduino/BME688 sensor bridge

  const renderPage = () => {
    switch (activePage) {
      case "home":
        return (
          <MovePage
            data={robotData}
            mqttAirQuality={mqttAirQuality}
            airPurifierOn={airPurifierOn}
            airPurifierMode={airPurifierMode}
            airPurifierSending={isAirSending}
            onToggleAirPurifier={toggleAirPurifier}
            onCycleAirPurifierMode={cycleAirPurifierMode}
            fragranceOn={fragranceOn}
            fragranceDiffusing={fragranceDiffusing}
            fragranceSending={isFragranceSending}
            onStopFragrance={handleStopFragrance}
            recommendedFragrance={recommendedFragrance}
            recommendedReason={recommendedReason}
            fragranceCartridgeRemaining={mockRobotData.fragranceCartridgeRemaining}
            lastDispenseAt={lastDispenseAt}
            onApplyFragrance={handleApplyFragrance}
          />
        );

      case "move":
        return (
          <ControlPage
            data={robotData}
            airPurifierOn={airPurifierOn}
            airPurifierMode={airPurifierMode}
            airPurifierSending={isAirSending}
            onToggleAirPurifier={toggleAirPurifier}
            onCycleAirPurifierMode={cycleAirPurifierMode}
            fragranceOn={fragranceOn}
            fragranceLevel={fragranceLevel}
            setFragranceLevel={setFragranceLevel}
            currentFragrance={currentFragrance}
            setCurrentFragrance={setCurrentFragrance}
            fragranceBlend={fragranceBlend}
            setFragranceBlend={setFragranceBlend}
            recommendedFragrance={recommendedFragrance}
            recommendedReason={recommendedReason}
            setRecommendedFragrance={setRecommendedFragrance}
            onApplyFragrance={handleApplyFragrance}
            mqttAirQuality={mqttAirQuality}
            fragranceCartridgeRemaining={mockRobotData.fragranceCartridgeRemaining}
            lastDispenseAt={lastDispenseAt}
            onAiSessionChange={handleAiSessionChange}
          />
        );

      case "more":
        return (
          <MorePage
            screensaverCharacterId={screensaverCharacterId}
            onSelectScreensaverCharacter={handleSelectScreensaverCharacter}
            airPurifierOn={airPurifierOn}
            airPurifierMode={airPurifierMode}
            airPurifierSending={isAirSending}
            onToggleAirPurifier={toggleAirPurifier}
            onSelectAirPurifierMode={selectAirPurifierMode}
            onSetAirPurifierStatus={syncAirPurifierStatus}
            fragranceOn={fragranceOn}
            fragranceChannels={fragranceChannels}
            onToggleFragranceChannel={toggleFragranceChannel}
            mqttAirQuality={mqttAirQuality}
            arduinoConnected={arduinoConnected}
            onReconnectArduino={reconnectArduino}
            foundingDemo={foundingDemo}
          />
        );

      default:
        return (
          <MovePage
            data={robotData}
            mqttAirQuality={mqttAirQuality}
            airPurifierOn={airPurifierOn}
            airPurifierMode={airPurifierMode}
            airPurifierSending={isAirSending}
            onToggleAirPurifier={toggleAirPurifier}
            onCycleAirPurifierMode={cycleAirPurifierMode}
            fragranceOn={fragranceOn}
            fragranceDiffusing={fragranceDiffusing}
            fragranceSending={isFragranceSending}
            onStopFragrance={handleStopFragrance}
            recommendedFragrance={recommendedFragrance}
            recommendedReason={recommendedReason}
            fragranceCartridgeRemaining={mockRobotData.fragranceCartridgeRemaining}
            lastDispenseAt={lastDispenseAt}
            onApplyFragrance={handleApplyFragrance}
          />
        );
    }
  };

  return (
    <div
      className={`app${hideBottomNav ? " immersive" : ""}`}
    >
      <Header
        variant={hideBottomNav ? "ai-consult" : "default"}
        onAiBack={aiBackHandler}
        onActivateScreensaver={activateScreensaver}
        screensaverIdleDisabled={screensaverIdleDisabled}
        onToggleScreensaverIdle={toggleScreensaverIdle}
        airPurifierOn={airPurifierOn}
        fragranceOn={fragranceOn}
        fragranceDiffusing={fragranceDiffusing}
        arduinoConnected={arduinoConnected}
        settingsOpen={activePage === "more"}
        onOpenSettings={() =>
          setActivePage((page) => (page === "more" ? "home" : "more"))
        }
      />

      <main className="screen-content">
        {renderPage()}
      </main>

      <EyeScreensaver
        active={screensaverActive}
        characterId={screensaverCharacterId}
        onDismiss={dismissScreensaver}
      />
    </div>
  );
}

export default App;
