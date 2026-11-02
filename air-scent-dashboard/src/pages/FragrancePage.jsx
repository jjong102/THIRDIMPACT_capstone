import { useState, useEffect } from "react";
import { Minus, Plus, SprayCan, Sparkles } from "lucide-react";

import FragranceBlendPage from "./FragranceBlendPage";
import AiRecommendPage from "./AiRecommendPage";
import "./FragrancePage.css";
import { scentLabel } from "../data/scentRecipes";

const blendPreviewNotes = [
  { id: "mint", label: "Peppermint", color: "#8b5cf6" },
  { id: "lavender", label: "Lavender", color: "#3b82f6" },
  { id: "orange", label: "Orange", color: "#d97706" },
];

function FragrancePage({
  fragranceOn,
  fragranceSending = false,
  onToggleFragrance,
  fragranceLevel,
  setFragranceLevel,
  currentFragrance,
  fragranceCartridgeRemaining,
  fragranceBlend,
  setFragranceBlend,
  recommendedFragrance,
  setRecommendedFragrance,
  data,
  entryView,
  onEntryViewClear,
}) {
  const [view, setView] = useState(() => entryView ?? "main");

  useEffect(() => {
    if (!entryView) {
      return undefined;
    }

    const frame = requestAnimationFrame(() => {
      setView(entryView);
      onEntryViewClear();
    });

    return () => cancelAnimationFrame(frame);
  }, [entryView, onEntryViewClear]);

  const levelText = {
    1: "약함",
    2: "보통",
    3: "강함",
  };

  const decreaseLevel = () => {
    setFragranceLevel((prev) => Math.max(1, prev - 1));
  };

  const increaseLevel = () => {
    setFragranceLevel((prev) => Math.min(3, prev + 1));
  };

  if (view === "blend") {
    return (
      <FragranceBlendPage
        selectedFragrance={currentFragrance}
        fragranceBlend={fragranceBlend}
        onSelect={(recipe) => {
          setFragranceBlend({ ...recipe.blend });
        }}
        onBack={() => setView("main")}
      />
    );
  }

  if (view === "ai") {
    return (
      <AiRecommendPage
        recommendedFragrance={recommendedFragrance}
        setRecommendedFragrance={setRecommendedFragrance}
        data={data}
        initialView="chat"
        onBack={() => setView("main")}
      />
    );
  }

  return (
    <section className="fragrance-page">
      <article
        className={`fragrance-hero-card ${fragranceOn ? "active" : "inactive"}`}
      >
        <div className="fragrance-hero-top">
          <div className="fragrance-icon-large">
            <SprayCan size={44} strokeWidth={2.1} />
          </div>

          <div className="fragrance-hero-text">
            <p className="section-label">현재 향기</p>
            <h1>{scentLabel(currentFragrance)}</h1>
            <span className={`fragrance-status-badge ${fragranceOn ? "on" : "off"}`}>
              {fragranceSending
                ? "전송 중..."
                : fragranceOn
                  ? "분사 중"
                  : "일시 정지"}
            </span>
            {!data.arduinoConnected && (
              <span className="fragrance-link-badge">아두이노 미연결</span>
            )}
          </div>

          <button
            className={`toggle-button ${fragranceOn ? "on" : "off"}`}
            type="button"
            disabled={fragranceSending}
            onClick={onToggleFragrance}
          >
            {fragranceOn ? "ON" : "OFF"}
          </button>
        </div>
      </article>

      <div className="fragrance-bottom-grid">
        <article className="fragrance-stat-card remaining-card">
          <p className="section-label">향기 잔량</p>

          <div className="remaining-bars">
            {blendPreviewNotes.map((note) => {
              const value = fragranceCartridgeRemaining[note.id];

              return (
                <div key={note.id} className="remaining-bar-item">
                  <strong style={{ color: note.color }}>{value}%</strong>

                  <div className="remaining-bar-track">
                    <div
                      className="remaining-bar-fill"
                      style={{
                        height: `${value}%`,
                        background: note.color,
                      }}
                    />
                  </div>

                  <span>{note.label}</span>
                </div>
              );
            })}
          </div>
        </article>

        <div className="fragrance-controls-panel">
          <article className="fragrance-stat-card">
            <div className="fragrance-stat-head">
              <p className="section-label">향기 강도</p>
              <strong>{levelText[fragranceLevel]}</strong>
            </div>

            <div className="level-control">
              <button type="button" onClick={decreaseLevel} aria-label="강도 낮추기">
                <Minus size={22} />
              </button>

              <div className="level-steps">
                {[1, 2, 3].map((step) => (
                  <span
                    key={step}
                    className={`level-step ${fragranceLevel >= step ? "active" : ""}`}
                  />
                ))}
              </div>

              <button type="button" onClick={increaseLevel} aria-label="강도 높이기">
                <Plus size={22} />
              </button>
            </div>
          </article>

          <button
            className="fragrance-action-card blend"
            type="button"
            onClick={() => setView("blend")}
          >
            <div className="fragrance-action-icon purple-soft">
              <SprayCan size={22} />
            </div>
            <div>
              <strong>향기 선택</strong>
              <span>Peppermint · Lavender · Orange</span>
            </div>
          </button>

          <button
            className="fragrance-action-card ai"
            type="button"
            onClick={() => setView("ai")}
          >
            <div className="fragrance-action-icon orange-soft">
              <Sparkles size={22} />
            </div>
            <div>
              <strong>AI 추천받기</strong>
              <span>다른 향 추천받기</span>
            </div>
          </button>
        </div>
      </div>
    </section>
  );
}

export default FragrancePage;
