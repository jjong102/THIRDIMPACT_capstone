import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Fan,
  SprayCan,
  Sparkles,
  Moon,
  Heart,
  House,
  Wind,
  Square,
} from "lucide-react";

import FragranceBlendPage from "./FragranceBlendPage";
import AiRecommendPage from "./AiRecommendPage";
import { scentDisplayName } from "../data/scentRecipes";
import { AIR_PURIFIER_MODE_LABELS } from "../utils/airPurifierCommand";
import {
  getWardScent,
  loadSelectedWardId,
  loadWardScents,
  persistWardScent,
  saveSelectedWardId,
  saveWardScents,
  wardDisplayName,
} from "../utils/wardStorage";
import {
  clampTargetPercent,
  labelForTargetPercent,
} from "../utils/fragranceIntensity";
import useSharedWards from "../hooks/useSharedWards";
import useScentMissionNav, {
  isNotLocalized,
  isScentMissionTo,
} from "../hooks/useScentMissionNav";
import "./FragrancePage.css";
import "./ControlPage.css";

function AirModeIconDisplay({ mode }) {
  if (mode === 2) {
    return <Moon size={26} strokeWidth={2.2} />;
  }

  if (mode === 3) {
    return (
      <span className="control-air-mode-badge" aria-hidden="true">
        <Heart size={16} strokeWidth={2.2} fill="currentColor" />
      </span>
    );
  }

  return (
    <span className="control-air-mode-badge" aria-hidden="true">
      A
    </span>
  );
}

function ControlPage({
  data,
  airPurifierOn,
  airPurifierMode,
  airPurifierSending = false,
  onToggleAirPurifier,
  onCycleAirPurifierMode,
  fragranceOn,
  setFragranceLevel,
  setCurrentFragrance,
  fragranceBlend,
  setFragranceBlend,
  recommendedFragrance,
  recommendedReason,
  setRecommendedFragrance,
  onApplyFragrance,
  mqttAirQuality,
  fragranceCartridgeRemaining,
  lastDispenseAt,
  onAiSessionChange,
}) {
  const [view, setView] = useState("main");
  const wards = useSharedWards();
  const [wardScents, setWardScents] = useState(loadWardScents);
  const [selectedWardId, setSelectedWardId] = useState(
    () => loadSelectedWardId(wards)
  );
  const refreshWards = useCallback(() => {
    setSelectedWardId((prev) => {
      if (prev && wards.some((ward) => ward.id === prev)) return prev;
      return loadSelectedWardId(wards);
    });
  }, [wards]);

  useEffect(() => {
    refreshWards();
  }, [refreshWards]);

  useEffect(() => {
    saveWardScents(wardScents);
  }, [wardScents]);

  const selectedWard = useMemo(
    () => wards.find((ward) => ward.id === selectedWardId) ?? null,
    [wards, selectedWardId]
  );

  const persistScent = useCallback((wardId, patch) => {
    if (!wardId) return;
    setWardScents(persistWardScent(wardId, patch));
  }, []);

  const applyScentToControls = useCallback(
    (scent) => {
      setFragranceLevel(scent.level);
      setFragranceBlend({ ...scent.blend });
      setCurrentFragrance?.(scent.fragrance);
    },
    [setFragranceBlend, setFragranceLevel, setCurrentFragrance]
  );

  const selectWard = useCallback(
    (wardId) => {
      setSelectedWardId(wardId);
      saveSelectedWardId(wardId);
      applyScentToControls(getWardScent(wardScents, wardId));
    },
    [applyScentToControls, wardScents]
  );

  const { mapMeta, navUi, navBusy, startScentMission } = useScentMissionNav({
    onSelectWard: selectWard,
  });
  const goingToSelected = Boolean(
    selectedWard && isScentMissionTo(navUi, selectedWard.id)
  );
  const notLocalized = isNotLocalized(navUi.locStatus);

  useEffect(() => {
    if (!selectedWardId) return;
    applyScentToControls(getWardScent(wardScents, selectedWardId));
    // 최초 마운트 시 선택 와드 향 반영
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (view === "ai") {
      onAiSessionChange?.({
        active: true,
        onBack: () => setView("main"),
      });
      return () => onAiSessionChange?.({ active: false, onBack: null });
    }

    onAiSessionChange?.({ active: false, onBack: null });
    return undefined;
  }, [view, onAiSessionChange]);

  const activeAirMode = airPurifierOn ? airPurifierMode : 1;

  const handleApplyFragrance = (payload) => {
    const wardId = selectedWardId || loadSelectedWardId(wards);
    const fragrance =
      typeof payload === "string" ? payload : payload?.fragrance;
    const patch = {};

    if (fragrance) patch.fragrance = scentDisplayName(fragrance);
    if (typeof payload === "object" && payload !== null) {
      if (payload.blend) patch.blend = payload.blend;
      if (payload.intensity) patch.level = clampTargetPercent(payload.intensity);
    }

    if (wardId && Object.keys(patch).length > 0) {
      persistScent(wardId, patch);
    }

    onApplyFragrance?.({
      ...(typeof payload === "object" && payload !== null ? payload : { fragrance }),
      wardId,
      wardName: selectedWard?.name,
    });
  };

  if (view === "blend") {
    return (
      <div className="control-subpage">
        <FragranceBlendPage
          selectedFragrance={
            selectedWard
              ? getWardScent(wardScents, selectedWard.id).fragrance
              : null
          }
          fragranceBlend={fragranceBlend}
          onSelect={(recipe) =>
            handleApplyFragrance({
              fragrance: recipe.displayName,
              blend: { ...recipe.blend },
              persistOnly: true,
            })
          }
          onBack={() => setView("main")}
        />
      </div>
    );
  }

  if (view === "ai") {
    return (
      <div className="control-subpage">
        <AiRecommendPage
          recommendedFragrance={recommendedFragrance}
          recommendedReason={recommendedReason}
          setRecommendedFragrance={setRecommendedFragrance}
          data={data}
          mqttAirQuality={mqttAirQuality}
          fragranceCartridgeRemaining={fragranceCartridgeRemaining}
          lastDispenseAt={lastDispenseAt}
          targetWardName={selectedWard?.name}
          targetWardId={selectedWardId}
          initialView="chat"
          onBack={() => setView("main")}
          onApplyRecommendation={handleApplyFragrance}
        />
      </div>
    );
  }

  return (
    <section className="control-page">
      <div className="control-panels">
        <article
          className={`control-panel control-panel-air ${airPurifierOn ? "is-on" : "is-off"}`}
        >
          <header className="control-panel-head air-head">
            <div className="control-panel-icon air">
              <Fan size={26} strokeWidth={2.1} />
            </div>
            <div className="control-panel-titles">
              <p className="section-label">공기청정</p>
              {!data.arduinoConnected && (
                <span className="control-warn-chip">미연결</span>
              )}
            </div>
          </header>

          <div className="control-panel-body control-air-body">
            {airPurifierOn ? (
              <>
                <button
                  type="button"
                  className={`control-mode-btn center mode-${airPurifierMode}`}
                  disabled={airPurifierSending}
                  onClick={onCycleAirPurifierMode}
                  aria-label="공기청정 모드 변경"
                >
                  <div className="control-mode-btn-icon">
                    <AirModeIconDisplay mode={activeAirMode} />
                  </div>
                  <strong>{AIR_PURIFIER_MODE_LABELS[airPurifierMode]}</strong>
                  <span>탭하여 모드 변경</span>
                </button>
                <button
                  type="button"
                  className="control-air-off-btn"
                  disabled={airPurifierSending}
                  onClick={onToggleAirPurifier}
                  aria-label="공기청정 끄기"
                >
                  OFF
                </button>
              </>
            ) : (
              <button
                type="button"
                className="control-mode-btn center off power-toggle"
                onClick={onToggleAirPurifier}
                aria-label="공기청정 켜기"
                aria-pressed={false}
              >
                <div className="control-mode-btn-icon">
                  <AirModeIconDisplay mode={1} />
                </div>
                <strong>{airPurifierSending ? "전송 중..." : "꺼짐"}</strong>
                <span>탭하여 켜기</span>
              </button>
            )}
          </div>
        </article>

        <article
          className={`control-panel control-panel-scent ${fragranceOn ? "is-on" : "is-off"} ${selectedWard ? "has-ward" : ""}`}
        >
          <header className="control-panel-head scent-head">
            <div className="control-panel-icon scent">
              <SprayCan size={26} strokeWidth={2.1} />
            </div>
            <div className="control-panel-titles">
              <p className="section-label">향기 설정</p>
              {!data.arduinoConnected && (
                <span className="control-warn-chip">미연결</span>
              )}
            </div>
          </header>

          <div className="control-panel-body control-scent-body">
            <div className="control-location-field">
              <div className="control-field-label">
                <span>위치별 향기</span>
                <strong>
                  {selectedWard ? wardDisplayName(selectedWard) : "집 없음"}
                </strong>
              </div>

              {wards.length === 0 ? (
                <p className="control-location-empty">
                  이동 탭에서 집 위치를 설정해 주세요
                </p>
              ) : (
                <div
                  className="control-location-tabs"
                  role="listbox"
                  aria-label="향기 적용 위치"
                >
                  {wards.map((ward) => {
                    const active = ward.id === selectedWardId;
                    const scent = getWardScent(wardScents, ward.id);
                    const wardOn = fragranceOn && active;
                    const goingHere = isScentMissionTo(navUi, ward.id);

                    return (
                      <button
                        key={ward.id}
                        type="button"
                        role="option"
                        aria-selected={active}
                        className={`control-location-tab ${active ? "active" : ""} ${wardOn ? "is-on" : ""} ${goingHere ? "is-going" : ""}`}
                        onClick={() => selectWard(ward.id)}
                      >
                        <span className="control-location-tab-icon">
                          <House size={18} strokeWidth={2.4} />
                        </span>
                        <span className="control-location-tab-text">
                          <span className="control-location-room">
                            {wardDisplayName(ward)}
                            {goingHere ? (
                              <em className="control-location-live">이동 중</em>
                            ) : wardOn ? (
                              <em className="control-location-live">분사 중</em>
                            ) : null}
                          </span>
                          <strong className="control-location-fragrance">
                            {scentDisplayName(scent.fragrance)}
                          </strong>
                          <em className="control-location-level">
                              {labelForTargetPercent(scent.level)} {clampTargetPercent(scent.level)}%
                          </em>
                        </span>
                      </button>
                    );
                  })}
                </div>
              )}

              <div className="control-scent-actions">
                <button
                  type="button"
                  className="control-scent-action blend"
                  disabled={!selectedWard}
                  onClick={() => setView("blend")}
                >
                  <SprayCan size={16} />
                  <span>향기 선택</span>
                </button>
                <button
                  type="button"
                  className="control-scent-action ai"
                  disabled={!selectedWard}
                  onClick={() => setView("ai")}
                >
                  <Sparkles size={16} />
                  <span>AI 추천받기</span>
                </button>
              </div>
            </div>

            <p className="control-blend-summary">
              선택 향기{" "}
              <strong>
                {scentDisplayName(
                  selectedWard
                    ? getWardScent(wardScents, selectedWard.id).fragrance
                    : null
                )}
              </strong>
            </p>

            <button
              type="button"
              className={`control-mission-btn ${goingToSelected ? "is-stop" : ""}`}
              onClick={() => void startScentMission(selectedWard)}
              title={
                notLocalized && !goingToSelected
                  ? "위치 추정이 필요합니다"
                  : undefined
              }
              disabled={
                !selectedWard ||
                navBusy ||
                (!goingToSelected && (notLocalized || !mapMeta))
              }
              aria-label={
                goingToSelected
                  ? `${selectedWard.name} 이동 정지`
                  : selectedWard
                    ? `${selectedWard.name} 발향 시작`
                    : "발향 시작"
              }
            >
              {goingToSelected ? (
                <Square size={18} strokeWidth={2.6} />
              ) : (
                <Wind size={18} strokeWidth={2.4} />
              )}
              <span>
                {goingToSelected
                  ? "정지"
                  : selectedWard
                    ? `${selectedWard.name} 발향 시작`
                    : "발향 시작"}
              </span>
            </button>
          </div>
        </article>
      </div>
    </section>
  );
}

export default ControlPage;
