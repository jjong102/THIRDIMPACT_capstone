import { useEffect, useMemo, useState } from "react";
import { House } from "lucide-react";

import {
  fetchPatrolZones,
  mapPercentToContainer,
  orientedRectToMapPolygon,
} from "../services/gdmMap";

function screenPoints(polygon, mapLayout) {
  return polygon
    .map(([x, y]) => {
      const screen = mapPercentToContainer(x, y, mapLayout);
      return `${(screen.x / 100) * mapLayout.containerW},${(screen.y / 100) * mapLayout.containerH}`;
    })
    .join(" ");
}

function average(polygon) {
  return [
    polygon.reduce((sum, point) => sum + point[0], 0) / polygon.length,
    polygon.reduce((sum, point) => sum + point[1], 0) / polygon.length,
  ];
}

function farthestEdgeMid(polygon, from) {
  let best = null;
  let bestDistance = -1;
  for (let index = 0; index < polygon.length; index += 1) {
    const start = polygon[index];
    const end = polygon[(index + 1) % polygon.length];
    const mid = [(start[0] + end[0]) / 2, (start[1] + end[1]) / 2];
    const distance = (mid[0] - from[0]) ** 2 + (mid[1] - from[1]) ** 2;
    if (distance > bestDistance) {
      best = mid;
      bestDistance = distance;
    }
  }
  return best;
}

function screenBottomCenter(polygon, mapLayout) {
  const screens = polygon.map(([x, y]) => mapPercentToContainer(x, y, mapLayout));
  const bottom = screens.slice().sort((a, b) => b.y - a.y).slice(0, 2);
  return {
    x: (bottom[0].x + bottom[1].x) / 2,
    y: (bottom[0].y + bottom[1].y) / 2,
  };
}

/**
 * 한계구역(파란 구간)과 정찰제한구역(주황 방)을 주행 지도 위에 그린다.
 * 정찰제한구역은 집 핀과 따로인 방 이름표로 둔다.
 */
export default function MapZoneOverlay({ mapLayout, mapMeta }) {
  const [zones, setZones] = useState(null);

  useEffect(() => {
    let cancelled = false;

    async function tick() {
      try {
        const next = await fetchPatrolZones();
        if (!cancelled) setZones(next);
      } catch {
        // 직전에 받은 구역을 유지한다.
      }
    }

    tick();
    const timer = window.setInterval(tick, 3000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, []);

  const shapes = useMemo(() => {
    if (!mapLayout || mapMeta?.source !== "robot" || !zones) return null;
    const boundary = orientedRectToMapPolygon(zones.boundary, mapMeta);
    const room = orientedRectToMapPolygon(zones.rectangle, mapMeta);
    if (!boundary && !room) return null;
    return { boundary, room };
  }, [mapLayout, mapMeta, zones]);

  if (!shapes) return null;

  const roomCenter = shapes.room ? average(shapes.room) : null;
  const boundaryAnchor = shapes.boundary
    ? farthestEdgeMid(shapes.boundary, roomCenter ?? average(shapes.boundary))
    : null;
  const roomScreen = shapes.room ? screenBottomCenter(shapes.room, mapLayout) : null;
  const boundaryScreen = boundaryAnchor
    ? mapPercentToContainer(boundaryAnchor[0], boundaryAnchor[1], mapLayout)
    : null;

  return (
    <>
      <svg className="slam-zones-svg" aria-hidden="true">
        {shapes.boundary ? (
          <polygon
            className="slam-zone-boundary"
            points={screenPoints(shapes.boundary, mapLayout)}
          />
        ) : null}
        {shapes.room ? (
          <polygon
            className="slam-zone-room"
            points={screenPoints(shapes.room, mapLayout)}
          />
        ) : null}
      </svg>

      {boundaryScreen ? (
        <div
          className="slam-zone-tag boundary"
          style={{ left: `${boundaryScreen.x}%`, top: `${boundaryScreen.y}%` }}
        >
          한계구역
        </div>
      ) : null}

      {roomScreen ? (
        <div
          className="slam-zone-room-badge"
          style={{ left: `${roomScreen.x}%`, top: `${roomScreen.y}%` }}
        >
          <House size={14} strokeWidth={2.4} aria-hidden="true" />
          <span>정찰제한구역</span>
        </div>
      ) : null}
    </>
  );
}
