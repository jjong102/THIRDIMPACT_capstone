import { useEffect, useMemo, useState } from "react";
import { CloudSun, House, SprayCan, Wind, Square } from "lucide-react";

import GdmSlamMap from "../components/GdmSlamMap";
import {
  getWardScent,
  loadSelectedWardId,
  loadWardScents,
  MAX_HOME_SCENT_WARDS,
  saveHomeScentWardIds,
  saveSelectedWardId,
  wardDisplayName,
} from "../utils/wardStorage";
import { getScentVisual, readingForWard, isKitchenWard } from "../utils/scentVisual";
import { scentDisplayName, scentLabel } from "../data/scentRecipes";
import {
  clampTargetPercent,
  labelForTargetPercent,
} from "../utils/fragranceIntensity";
import useSharedWards from "../hooks/useSharedWards";
import useScentMissionNav, {
  isNotLocalized,
  isScentMissionTo,
} from "../hooks/useScentMissionNav";
import { stopSharedFragranceSpray } from "../services/moveUi";
import "./HomePage.css";

function formatLevel(level) {
  const pct = clampTargetPercent(level);
  return `${labelForTargetPercent(pct)} ${pct}%`;
}

function formatOutdoorMeta(outdoor) {
  if (outdoor.loading && !outdoor.hasData) {
    return "외부 날씨·공기질 불러오는 중…";
  }

  if (!outdoor.hasData) {
    return outdoor.error ?? "외부 환경 정보를 불러오지 못했습니다";
  }

  const parts = [];

  if (outdoor.temperature != null) {
    parts.push(`${outdoor.temperature}°`);
  }

  if (outdoor.weatherLabel) {
    parts.push(outdoor.weatherLabel);
  }

  if (outdoor.aqi != null) {
    parts.push(`AQI ${outdoor.aqi}`);
  }

  if (outdoor.pm25 != null) {
    parts.push(`PM2.5 ${outdoor.pm25}`);
  }

  if (outdoor.humidity != null) {
    parts.push(`습도 ${outdoor.humidity}%`);
  }

  return parts.join(" · ");
}

function globalReading(mqttAirQuality) {
  if (!mqttAirQuality?.hasData && !mqttAirQuality?.label) return null;
  return {
    label: mqttAirQuality.label,
    confidence: mqttAirQuality.confidence,
    isOrange: mqttAirQuality.isOrange,
    tone: mqttAirQuality.tone,
    receivedAt: mqttAirQuality.receivedAt,
  };
}

function pickHomeWards(wards) {
  return wards.slice(0, MAX_HOME_SCENT_WARDS);
}

function HomePage({
  data,
  fragranceOn,
  fragranceDiffusing,
  fragranceDispenseComplete,
  mqttAirQuality,
  outdoorEnvironment,
  onStopFragrance,
}) {
  const outdoor = outdoorEnvironment ?? { loading: true, hasData: false };
  const wards = useSharedWards();
  const [wardScents, setWardScents] = useState(loadWardScents);
  const { navUi, navBusy, mapMeta, startScentMission } = useScentMissionNav();
  const [pickedWardId, setPickedWardId] = useState(() => loadSelectedWardId());
  const [stopBusy, setStopBusy] = useState(false);
  const spraying = Boolean(
    navUi.fragranceSpraying || fragranceOn || fragranceDiffusing
  );
  const sprayWardId = navUi.fragranceWardId || navUi.selectedId || null;

  useEffect(() => {
    const refresh = () => {
      setWardScents(loadWardScents());
      setPickedWardId((prev) => loadSelectedWardId() || prev);
    };
    refresh();
    window.addEventListener("focus", refresh);
    window.addEventListener("air-scent-ward-scents-changed", refresh);
    window.addEventListener("air-scent-selected-ward-changed", refresh);
    return () => {
      window.removeEventListener("focus", refresh);
      window.removeEventListener("air-scent-ward-scents-changed", refresh);
      window.removeEventListener("air-scent-selected-ward-changed", refresh);
    };
  }, []);

  const homeWards = useMemo(() => pickHomeWards(wards), [wards]);

  useEffect(() => {
    saveHomeScentWardIds(homeWards.map((ward) => ward.id));
  }, [homeWards]);

  const fallbackReading = globalReading(mqttAirQuality);

  const scentSlots = useMemo(() => {
    const slots = homeWards.map((ward) => {
      const reading = readingForWard(
        mqttAirQuality.rooms,
        ward,
        fallbackReading
      );
      const visual = getScentVisual(reading, {
        connected: isKitchenWard(ward) || mqttAirQuality.connected,
      });
      const scent = getWardScent(wardScents, ward.id);
      return { ward, reading, visual, scent };
    });

    while (slots.length < MAX_HOME_SCENT_WARDS) {
      slots.push({
        ward: null,
        reading: null,
        visual: null,
        scent: null,
      });
    }

    return slots;
  }, [homeWards, mqttAirQuality, fallbackReading, wardScents]);

  const outdoorTitle = outdoor.hasData
    ? outdoor.airStatus
    : outdoor.loading
      ? "불러오는 중"
      : "연결 실패";

  const outdoorTone = outdoor.hasData ? outdoor.airTone : "unknown";
  const outdoorAqi =
    outdoor.aqi != null ? outdoor.aqi : outdoor.loading ? "…" : "—";

  const wardFragranceStatus = (scent, wardId) => {
    if (!scent) return { label: "대기", tone: "idle" };

    const sprayingHere =
      spraying && sprayWardId && String(sprayWardId) === String(wardId);
    const isThisFragrance =
      sprayingHere ||
      (data.currentFragrance === scent.fragrance &&
        (fragranceOn || fragranceDiffusing || fragranceDispenseComplete));

    if ((fragranceDiffusing || sprayingHere) && isThisFragrance) {
      return { label: "발향 중", tone: "diffusing" };
    }
    if (fragranceDispenseComplete && isThisFragrance) {
      return { label: "분사 완료", tone: "complete" };
    }
    if (fragranceOn && isThisFragrance) {
      return { label: "분사 중", tone: "on" };
    }
    return { label: "대기", tone: "idle" };
  };

  const goingWardId = isScentMissionTo(navUi, navUi.selectedId)
    ? navUi.selectedId
    : spraying
      ? sprayWardId
      : null;
  const activeWardId = goingWardId || pickedWardId;
  const activeWard = wards.find((ward) => ward.id === activeWardId) ?? null;
  const goingToActive = Boolean(
    activeWard && isScentMissionTo(navUi, activeWard.id)
  );
  const stoppingActive = Boolean(
    spraying && activeWard && String(sprayWardId) === String(activeWard.id)
  );

  const notLocalized = isNotLocalized(navUi.locStatus);

  const handleHomeGo = () => {
    if (!activeWard) return;
    if (stoppingActive) {
      if (stopBusy) return;
      setStopBusy(true);
      onStopFragrance?.();
      void stopSharedFragranceSpray().finally(() => setStopBusy(false));
      return;
    }
    if (goingToActive) {
      void startScentMission(activeWard);
      return;
    }
    if (notLocalized) return;
    void startScentMission(activeWard);
  };

  const pickWard = (ward) => {
    if (!ward) return;
    setPickedWardId(ward.id);
    saveSelectedWardId(ward.id);
  };

  return (
    <section className="home-page">
      <article className={`home-outdoor-hero outdoor-${outdoorTone}`}>
        <div className="home-outdoor-main">
          <div className="home-hero-label-row">
            <p className="section-label">외부 환경</p>
            <span className="home-hero-place">
              <CloudSun size={14} strokeWidth={2.4} />
              {outdoor.place ?? "인천대학교"}
            </span>
          </div>

          <h1 className="home-outdoor-title">{outdoorTitle}</h1>
          <p className="home-outdoor-meta">{formatOutdoorMeta(outdoor)}</p>
        </div>

        <div className="home-outdoor-aqi">
          <strong>{outdoorAqi}</strong>
          <span>AQI</span>
        </div>
      </article>

      <div className="home-main-split">
        <div className="home-ward-column">
          {scentSlots.map((slot, index) => {
            if (!slot.ward) {
              return (
                <article
                  key={`empty-${index}`}
                  className="home-ward-card empty"
                >
                  <House size={22} strokeWidth={2.2} />
                  <strong>집 {index + 1}</strong>
                  <em>홈 탭에서 집을 만들어 주세요</em>
                </article>
              );
            }

            const { ward, visual, scent } = slot;
            const fragranceStatus = wardFragranceStatus(scent, ward.id);
            const goingHere = isScentMissionTo(navUi, ward.id);
            const selected = ward.id === activeWardId;

            return (
              <article
                key={ward.id}
                className={`home-ward-card tone-${visual.tone}${goingHere ? " is-going" : ""}${selected ? " is-selected" : ""}`}
                style={{
                  ["--scent-glow"]: visual.glow,
                  ["--scent-soft"]: visual.soft,
                  ["--scent-color"]: visual.color,
                }}
              >
                <button
                  type="button"
                  className="home-ward-card-hit"
                  onClick={() => pickWard(ward)}
                  aria-pressed={selected}
                  aria-label={`${ward.name} 선택`}
                >
                  <div className="home-ward-card-top">
                    <span className="home-ward-card-name">
                      <House size={14} strokeWidth={2.4} />
                      {wardDisplayName(ward)}
                    </span>
                    <span
                      className={`home-ward-fragrance-chip ${goingHere ? "going" : fragranceStatus.tone}`}
                    >
                      {goingHere ? "이동 중" : fragranceStatus.label}
                    </span>
                  </div>

                  <div className="home-ward-card-body">
                    <div className="home-ward-card-emoji" aria-hidden="true">
                      {visual.emoji}
                    </div>
                    <div className="home-ward-card-text">
                      <strong>{visual.title}</strong>
                      <em>{visual.detail}</em>
                      <p className="home-ward-fragrance-line">
                        <SprayCan size={14} strokeWidth={2.4} />
                        {scentLabel(scentDisplayName(scent.fragrance))}
                        <span>· {formatLevel(scent.level)}</span>
                      </p>
                    </div>
                  </div>
                </button>
              </article>
            );
          })}
        </div>

        <article className="home-map-card">
          <header className="home-map-head">
            <p className="section-label">현장 지도</p>
            <span className="slam-live-chip">
              <span className="slam-live-dot" aria-hidden="true" />
              GDM SLAM
            </span>
          </header>
          <div className="home-map-stage">
            <GdmSlamMap
              mqttAirQuality={mqttAirQuality}
              wards={wards}
              selectedWardId={activeWardId}
              goingWardId={goingWardId}
              onWardClick={pickWard}
            />
            {activeWard ? (
              <div className="home-map-go">
                <button
                  type="button"
                  className={`home-go-btn ${goingToActive || stoppingActive ? "is-stop" : "scent-mission"}${navBusy || stopBusy ? " busy" : ""}`}
                  disabled={
                    navBusy ||
                    stopBusy ||
                    (!goingToActive &&
                      !stoppingActive &&
                      (notLocalized || !mapMeta))
                  }
                  onClick={handleHomeGo}
                  title={
                    notLocalized && !goingToActive && !stoppingActive
                      ? "위치 추정이 필요합니다"
                      : undefined
                  }
                  aria-label={
                    stoppingActive
                      ? `${activeWard.name} 발향 중지`
                      : goingToActive
                        ? `${activeWard.name} 발향 정지`
                        : `${activeWard.name} 발향 시작`
                  }
                >
                  {goingToActive || stoppingActive ? (
                    <Square size={16} strokeWidth={2.4} fill="currentColor" />
                  ) : (
                    <Wind size={16} strokeWidth={2.4} />
                  )}
                  <span>
                    {stoppingActive
                      ? stopBusy
                        ? "중지 중…"
                        : "발향 중지"
                      : goingToActive
                        ? navBusy
                          ? "정지 중…"
                          : "정지"
                        : navBusy
                          ? "전송 중…"
                          : "발향 시작"}
                  </span>
                </button>
              </div>
            ) : null}
          </div>
        </article>
      </div>
    </section>
  );
}

export default HomePage;
