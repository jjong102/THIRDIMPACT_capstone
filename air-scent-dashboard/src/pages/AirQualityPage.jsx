import { Fan, SprayCan, Sparkles, Droplets } from "lucide-react";

import "./AirQualityPage.css";
import { scentLabel } from "../data/scentRecipes";

function AirQualityPage({
  data,
  airPurifierOn,
  airPurifierSending = false,
  onToggleAirPurifier,
  onChangePage,
  onOpenFragranceAi,
  currentFragrance,
}) {
  return (
    <section className="air-page">
      <div className="air-top-grid">
        <article className="aqi-card">
          <div>
            <p className="section-label">현재 공기 상태</p>
            <h1 className="air-status-gradient">{data.airStatus}</h1>
          </div>

          <div className="aqi-number-wrap">
            <strong className="aqi-number">{data.airQuality}</strong>
            <span className="aqi-label">AQI</span>
          </div>
        </article>

        <article className="summary-card">
          <p className="section-label">환경 요약</p>

          <div className="summary-list">
            <div className="summary-item">
              <div className="summary-icon blue-soft">
                <Fan size={22} />
              </div>
              <div>
                <span>PM2.5</span>
                <strong>{data.pm25} μg/m³</strong>
              </div>
            </div>

            <div className="summary-item">
              <div className="summary-icon purple-soft">
                <Droplets size={22} />
              </div>
              <div>
                <span>습도</span>
                <strong>{data.humidity}%</strong>
              </div>
            </div>
          </div>
        </article>
      </div>

      <div className="air-action-grid">
        <button
          className={`air-action-card blue-action ${airPurifierOn ? "active" : ""}`}
          type="button"
          disabled={airPurifierSending}
          onClick={onToggleAirPurifier}
        >
          <div className="action-icon">
            <Fan size={28} />
          </div>
          <div>
            <span>공기청정</span>
            <strong>
              {airPurifierSending ? "..." : airPurifierOn ? "ON" : "OFF"}
            </strong>
          </div>
        </button>

        <button
          className="air-action-card purple-action"
          type="button"
          onClick={() => onChangePage("fragrance")}
        >
          <div className="action-icon">
            <SprayCan size={28} />
          </div>
          <div>
            <span>향기</span>
            <strong>제어하기</strong>
          </div>
        </button>

        <button
          className="air-action-card orange-action"
          type="button"
          onClick={onOpenFragranceAi}
        >
          <div className="action-icon">
            <Sparkles size={28} />
          </div>
          <div>
            <span>AI 추천</span>
            <strong>보기</strong>
          </div>
        </button>
      </div>

      <p className="air-fragrance-note">
        현재 향기: <strong>{scentLabel(currentFragrance)}</strong>
        {!data.arduinoConnected && (
          <>
            {" "}
            · <span className="air-link-warning">아두이노 미연결</span>
          </>
        )}
      </p>
    </section>
  );
}

export default AirQualityPage;
