import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Bot, House } from "lucide-react";

import useActiveRobotMap from "../hooks/useActiveRobotMap";
import useGdmRobotPose from "../hooks/useGdmRobotPose";
import {
  GDM_FLOORPLAN_URL,
  computeContainLayout,
  displayYawCss,
  fetchGdmRooms,
  mapPercentToContainer,
  poseToMapPercent,
  roomsToWards,
} from "../services/gdmMap";
import { getScentVisual, readingForWard, isKitchenWard } from "../utils/scentVisual";
import { wardDisplayName } from "../utils/wardStorage";
import MapZoneOverlay from "./MapZoneOverlay";
import "../pages/MovePage.css";

/**
 * Read-only GDM floorplan with robot pose, room outlines, and ward scent markers.
 * Matches the Move tab map visuals.
 */
function GdmSlamMap({
  mqttAirQuality,
  className = "",
  selectedWardId = null,
  goingWardId = null,
  wards = [],
  onWardClick = null,
}) {
  const mapRef = useRef(null);
  const imgRef = useRef(null);
  const [mapReady, setMapReady] = useState(false);
  const [mapFailed, setMapFailed] = useState(false);
  const [mapLayout, setMapLayout] = useState(null);
  const [mapMeta, setMapMeta] = useState(null);
  const [roomPolys, setRoomPolys] = useState([]);

  const { pose: gdmPose, status: gdmStatus } = useGdmRobotPose(true);
  const robotMap = useActiveRobotMap();

  useEffect(() => {
    if (robotMap.status === "loading") return undefined;
    if (robotMap.meta) {
      setMapMeta(robotMap.meta);
      setRoomPolys([]);
      return undefined;
    }

    let cancelled = false;

    (async () => {
      try {
        const payload = await fetchGdmRooms();
        if (cancelled) return;
        setMapMeta({
          resolution: payload?.resolution ?? null,
          origin: payload?.origin ?? null,
          image_size: payload?.image_size ?? null,
          map: payload?.map ?? null,
        });
        const nextWards = roomsToWards(payload);
        setRoomPolys(
          nextWards
            .filter((ward) => ward.polygon?.length)
            .map((ward) => ({
              id: ward.id,
              polygon: ward.polygon,
            }))
        );
      } catch (error) {
        console.warn("[home-map] GDM rooms load failed:", error);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [robotMap.status, robotMap.signature, robotMap.meta]);

  const mapRotation = robotMap.rotation || 0;
  const mapZoom = robotMap.zoom || 100;
  const mapCenterX = Number.isFinite(Number(robotMap.centerX)) ? Number(robotMap.centerX) : 50;
  const mapCenterY = Number.isFinite(Number(robotMap.centerY)) ? Number(robotMap.centerY) : 50;

  const updateLayout = useCallback(() => {
    const container = mapRef.current;
    const img = imgRef.current;
    if (!container || !img?.naturalWidth) return;

    setMapLayout(
      computeContainLayout(
        container.clientWidth,
        container.clientHeight,
        img.naturalWidth,
        img.naturalHeight,
        mapRotation,
        mapZoom,
        mapCenterX,
        mapCenterY,
      )
    );
  }, [mapRotation, mapZoom, mapCenterX, mapCenterY]);

  useLayoutEffect(() => {
    if (!mapReady) return undefined;
    updateLayout();
    const container = mapRef.current;
    if (!container || typeof ResizeObserver === "undefined") return undefined;

    const observer = new ResizeObserver(() => updateLayout());
    observer.observe(container);
    return () => observer.disconnect();
  }, [mapReady, updateLayout]);

  const liveRobotMap = useMemo(() => {
    if (!mapLayout) return null;
    return poseToMapPercent(
      gdmPose,
      mapLayout.naturalW,
      mapLayout.naturalH,
      mapMeta
    );
  }, [gdmPose, mapLayout, mapMeta]);

  const robotMapPos = liveRobotMap?.live ? liveRobotMap : null;
  const robotScreen = robotMapPos
    ? mapPercentToContainer(robotMapPos.x, robotMapPos.y, mapLayout)
    : null;

  const fallbackReading =
    mqttAirQuality?.hasData || mqttAirQuality?.label
      ? {
          label: mqttAirQuality.label,
          confidence: mqttAirQuality.confidence,
          isOrange: mqttAirQuality.isOrange,
          tone: mqttAirQuality.tone,
        }
      : null;

  const wardVisuals = useMemo(() => {
    const rooms = mqttAirQuality?.rooms ?? {};
    const map = {};
    const fallback =
      fallbackReading ??
      (mqttAirQuality?.label
        ? {
            label: mqttAirQuality.label,
            confidence: mqttAirQuality.confidence,
            isOrange: mqttAirQuality.isOrange,
            tone: mqttAirQuality.tone,
            raw: mqttAirQuality.label,
          }
        : null);

    for (const ward of wards) {
      let reading = readingForWard(rooms, ward, null);
      if (!reading && !isKitchenWard(ward)) {
        reading = rooms.default ?? fallback;
      }
      map[ward.id] = getScentVisual(reading, {
        connected: isKitchenWard(ward) || Boolean(mqttAirQuality?.connected),
      });
    }

    return map;
  }, [wards, mqttAirQuality, fallbackReading]);

  return (
    <div
      ref={mapRef}
      className={`slam-map home-gdm-map ${className}`.trim()}
      style={{
        "--map-rotation": `${mapLayout?.rotation ?? mapRotation}deg`,
        "--map-view-scale": mapLayout?.viewScale ?? mapZoom / 100,
        "--map-pan-x": `${mapLayout?.panX ?? 0}px`,
        "--map-pan-y": `${mapLayout?.panY ?? 0}px`,
      }}
      role="img"
      aria-label="GDM SLAM 지도"
    >
      <div className="slam-map-surface" aria-hidden="true">
        <div className="slam-map-base" />
        <div className="slam-map-grid" />
        {!mapFailed && robotMap.status !== "loading" && (
          <img
            ref={imgRef}
            className={`slam-map-image ${mapReady ? "ready" : ""}`}
            src={
              robotMap.imageUrl ??
              `${GDM_FLOORPLAN_URL}?t=${gdmStatus.map ?? "map"}`
            }
            alt=""
            draggable={false}
            onLoad={() => {
              setMapReady(true);
              setMapFailed(false);
              requestAnimationFrame(updateLayout);
            }}
            onError={() => {
              setMapFailed(true);
              setMapReady(false);
            }}
          />
        )}
        <div className="slam-map-vignette" />
        <div
          className={`slam-map-fallback ${mapFailed || !mapReady ? "visible" : ""}`}
        >
          <p className="slam-fallback-label">
            {mapFailed
              ? robotMap.imageUrl
                ? "주행 맵을 불러오지 못했습니다"
                : "GDM floor_plan PNG를 찾을 수 없습니다 (gdm-bridge 확인)"
              : "지도 불러오는 중…"}
          </p>
        </div>
      </div>

      {mapLayout && (
        <svg className="slam-rooms-svg" aria-hidden="true">
          <defs>
            {roomPolys.map((room) => {
              const poly = room.polygon ?? [];
              if (!Array.isArray(poly[0])) return null;
              const points = poly
                .map(([x, y]) => {
                  const screen = mapPercentToContainer(x, y, mapLayout);
                  return `${(screen.x / 100) * mapLayout.containerW},${(screen.y / 100) * mapLayout.containerH}`;
                })
                .join(" ");
              if (!points) return null;
              return (
                <clipPath key={`clip-${room.id}`} id={`home-ward-clip-${room.id}`}>
                  <polygon points={points} />
                </clipPath>
              );
            })}
            {wards.map((ward) => {
              const visual = wardVisuals[ward.id];
              if (
                !visual ||
                visual.tone === "waiting" ||
                visual.tone === "offline"
              ) {
                return null;
              }
              return (
                <radialGradient
                  key={`grad-${ward.id}`}
                  id={`home-scent-grad-${ward.id}`}
                  cx="50%"
                  cy="50%"
                  r="50%"
                >
                  <stop offset="0%" stopColor={visual.color} stopOpacity="0.55" />
                  <stop offset="45%" stopColor={visual.color} stopOpacity="0.22" />
                  <stop offset="100%" stopColor={visual.color} stopOpacity="0" />
                </radialGradient>
              );
            })}
          </defs>

          {roomPolys.map((room) => {
            const poly = room.polygon ?? [];
            const wardId = room.id;
            const visual = wardId ? wardVisuals[wardId] : null;
            const activeScent =
              visual &&
              visual.tone !== "waiting" &&
              visual.tone !== "offline";
            if (!Array.isArray(poly[0])) return null;

            const points = poly
              .map(([x, y]) => {
                const screen = mapPercentToContainer(x, y, mapLayout);
                return `${(screen.x / 100) * mapLayout.containerW},${(screen.y / 100) * mapLayout.containerH}`;
              })
              .join(" ");
            if (!points) return null;

            const clickableWard = onWardClick
              ? wards.find((ward) => ward.id === wardId)
              : null;

            return (
              <polygon
                key={`outline-${wardId ?? points}`}
                points={points}
                className={`slam-room-poly ${activeScent ? `scent-${visual.tone}` : ""}${clickableWard ? " clickable" : ""}`}
                style={activeScent ? { stroke: visual.color } : undefined}
                onClick={
                  clickableWard
                    ? (event) => {
                        event.stopPropagation();
                        onWardClick(clickableWard);
                      }
                    : undefined
                }
              />
            );
          })}

          {wards.map((ward) => {
            const visual = wardVisuals[ward.id];
            const hasScent =
              visual &&
              visual.tone !== "waiting" &&
              visual.tone !== "offline";
            if (!hasScent) return null;

            const screen = mapPercentToContainer(ward.x, ward.y, mapLayout);
            const cx = (screen.x / 100) * mapLayout.containerW;
            const cy = (screen.y / 100) * mapLayout.containerH;
            const radius = Math.min(
              200,
              Math.max(
                130,
                Math.min(mapLayout.width, mapLayout.height) * 0.32
              )
            );
            const hasClip = roomPolys.some((room) => room.id === ward.id);

            return (
              <g
                key={`scent-${ward.id}`}
                clipPath={
                  hasClip ? `url(#home-ward-clip-${ward.id})` : undefined
                }
              >
                <circle
                  className="slam-local-scent"
                  cx={cx}
                  cy={cy}
                  r={radius}
                  fill={`url(#home-scent-grad-${ward.id})`}
                />
                <circle
                  className="slam-local-scent-core"
                  cx={cx}
                  cy={cy}
                  r={radius * 0.42}
                  fill={visual.glow}
                />
              </g>
            );
          })}
        </svg>
      )}

      <MapZoneOverlay mapLayout={mapLayout} mapMeta={mapMeta} />

      {robotScreen ? (
        <div
          className="slam-robot live"
          style={{
            left: `${robotScreen.x}%`,
            top: `${robotScreen.y}%`,
          }}
          title={
            robotMapPos?.source === "amcl" ||
            robotMapPos?.source === "initialpose"
              ? "AMCL 로봇 위치"
              : "GDM 실시간 로봇 위치"
          }
        >
          <span
            className="slam-robot-heading"
              style={{
                transform: `rotate(${displayYawCss(robotMapPos?.yaw ?? 0, mapLayout?.rotation)}deg)`,
              }}
            aria-hidden="true"
          >
            <span className="slam-robot-nose" />
          </span>
          <span className="slam-robot-inner">
            <Bot size={18} strokeWidth={2.4} />
          </span>
        </div>
      ) : null}

      {wards.map((ward) => {
        const active = ward.id === selectedWardId;
        const going = ward.id === goingWardId;
        const screen = mapPercentToContainer(ward.x, ward.y, mapLayout);
        const visual = wardVisuals[ward.id];
        const hasScent =
          visual &&
          visual.tone !== "waiting" &&
          visual.tone !== "offline";
        const PinTag = onWardClick ? "button" : "div";

        return (
          <div
            key={ward.id}
            className="slam-ward-wrap"
            style={{ left: `${screen.x}%`, top: `${screen.y}%` }}
          >
            <span className="slam-ward-point" aria-hidden="true">
              <i />
            </span>

            {hasScent && (
              <div
                className={`slam-ward-aura tone-${visual.tone}`}
                style={{
                  ["--aura-glow"]: visual.glow,
                  ["--aura-soft"]: visual.soft,
                  ["--aura-color"]: visual.color,
                }}
                aria-hidden="true"
              >
                <span className="slam-ward-aura-ring" />
                <span className="slam-ward-aura-ring delay" />
              </div>
            )}
            {hasScent && (
              <div className="slam-scent-burst" aria-hidden="true">
                {[0, 60, 120, 180, 240, 300].map((angle, index) => (
                  <span
                    key={angle}
                    className="slam-scent-burst-emoji"
                    style={{
                      "--burst-angle": `${angle}deg`,
                      "--burst-delay": `${index * 0.42}s`,
                    }}
                  >
                    {visual.emoji}
                  </span>
                ))}
              </div>
            )}

            <PinTag
              {...(onWardClick
                ? {
                    type: "button",
                    onClick: () => onWardClick(ward),
                    "aria-label": `${ward.name} 선택`,
                  }
                : {
                    "aria-label": `${ward.name} 위치`,
                  })}
              className={`slam-ward ${active ? "selected" : ""} ${going ? "is-going" : ""}`}
            >
              <span className="slam-ward-pin" aria-hidden="true">
                <span className="slam-ward-pin-head">
                  {hasScent ? (
                    <span className="slam-ward-emoji">{visual.emoji}</span>
                  ) : (
                    <House size={active ? 20 : 18} strokeWidth={2.4} />
                  )}
                </span>
                <span className="slam-ward-pin-tip" />
              </span>
              <span className="slam-ward-label">
                {wardDisplayName(ward)}
                {going ? " · 이동 중" : hasScent ? ` · ${visual.short}` : ""}
              </span>
            </PinTag>
          </div>
        );
      })}
    </div>
  );
}

export default GdmSlamMap;
