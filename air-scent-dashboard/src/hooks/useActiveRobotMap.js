import { useEffect, useMemo, useRef, useState } from "react";

import {
  displayedCenter,
  displayedRotation,
  displayedZoom,
  MAP_CENTER_DEFAULT,
  MAP_CENTER_EVENT,
  MAP_ROTATION_EVENT,
  MAP_ZOOM_EVENT,
} from "../services/mapRotation";
import { fetchRobotMaps, robotMapPreviewUrl } from "../services/robotMaps";

export const ROBOT_MAP_EVENT = "air-scent-robot-map";

function mapSignature(map) {
  if (!map) return "";
  return [
    map.name,
    map.updatedAt,
    map.width,
    map.height,
    map.resolution,
    map.rotation ?? "",
    map.zoom ?? "",
    map.centerX ?? "",
    map.centerY ?? "",
    Array.isArray(map.origin) ? map.origin.join(",") : "",
  ].join("|");
}

export function robotMapToMeta(map) {
  if (
    !map?.width ||
    !map?.height ||
    map.resolution == null ||
    !Array.isArray(map.origin) ||
    map.origin.length < 2
  ) {
    return null;
  }

  return {
    resolution: map.resolution,
    origin: map.origin,
    image_size: [map.width, map.height],
    map: map.name,
    source: "robot",
  };
}

function withView(map) {
  if (!map?.name) return map;
  const rotation = displayedRotation(map.name, map.rotation);
  const zoom = displayedZoom(map.name, map.zoom);
  const center = displayedCenter(map.name, map.centerX, map.centerY);
  if (
    map.rotation === rotation &&
    map.zoom === zoom &&
    map.centerX === center.x &&
    map.centerY === center.y
  ) {
    return map;
  }
  return { ...map, rotation, zoom, centerX: center.x, centerY: center.y };
}

export default function useActiveRobotMap() {
  const [status, setStatus] = useState("loading");
  const [map, setMap] = useState(null);
  const mapNameRef = useRef(null);
  mapNameRef.current = map?.name ?? null;

  useEffect(() => {
    let cancelled = false;

    const load = async () => {
      try {
        const payload = await fetchRobotMaps();
        if (cancelled) return;
        const maps = Array.isArray(payload.maps) ? payload.maps : [];
        const active = withView(
          maps.find((item) => item.name === payload.active) ?? null,
        );
        setMap((prev) => (mapSignature(prev) === mapSignature(active) ? prev : active));
        setStatus("ready");
      } catch {
        if (cancelled) return;
        setStatus("error");
      }
    };

    const onRotation = (event) => {
      const name = event.detail?.name;
      const degrees = Number(event.detail?.degrees);
      if (!name || name !== mapNameRef.current || !Number.isFinite(degrees)) return;
      setMap((prev) => {
        if (!prev || prev.name !== name || prev.rotation === degrees) return prev;
        return { ...prev, rotation: degrees };
      });
    };

    const onZoom = (event) => {
      const name = event.detail?.name;
      const zoom = Number(event.detail?.zoom);
      if (!name || name !== mapNameRef.current || !Number.isFinite(zoom)) return;
      setMap((prev) => {
        if (!prev || prev.name !== name || prev.zoom === zoom) return prev;
        return { ...prev, zoom };
      });
    };

    const onCenter = (event) => {
      const name = event.detail?.name;
      const centerX = Number(event.detail?.x);
      const centerY = Number(event.detail?.y);
      if (
        !name ||
        name !== mapNameRef.current ||
        !Number.isFinite(centerX) ||
        !Number.isFinite(centerY)
      ) {
        return;
      }
      setMap((prev) => {
        if (!prev || prev.name !== name || (prev.centerX === centerX && prev.centerY === centerY)) {
          return prev;
        }
        return { ...prev, centerX, centerY };
      });
    };

    load();
    const timer = window.setInterval(load, 8000);
    window.addEventListener(ROBOT_MAP_EVENT, load);
    window.addEventListener(MAP_ROTATION_EVENT, onRotation);
    window.addEventListener(MAP_ZOOM_EVENT, onZoom);
    window.addEventListener(MAP_CENTER_EVENT, onCenter);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
      window.removeEventListener(ROBOT_MAP_EVENT, load);
      window.removeEventListener(MAP_ROTATION_EVENT, onRotation);
      window.removeEventListener(MAP_ZOOM_EVENT, onZoom);
      window.removeEventListener(MAP_CENTER_EVENT, onCenter);
    };
  }, []);

  const meta = useMemo(() => robotMapToMeta(map), [map]);
  const imageUrl = map?.pgm ? robotMapPreviewUrl(map.name, map.updatedAt) : null;

  return {
    status,
    map,
    meta,
    imageUrl,
    rotation: Number(map?.rotation) || 0,
    zoom: Number(map?.zoom) || 100,
    centerX: Number.isFinite(Number(map?.centerX)) ? Number(map.centerX) : MAP_CENTER_DEFAULT,
    centerY: Number.isFinite(Number(map?.centerY)) ? Number(map.centerY) : MAP_CENTER_DEFAULT,
    signature: mapSignature(map),
  };
}
