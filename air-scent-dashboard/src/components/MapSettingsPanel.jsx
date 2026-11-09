import { Check, Map, RefreshCw } from "lucide-react";
import { useEffect, useLayoutEffect, useRef, useState } from "react";

import { ROBOT_MAP_EVENT } from "../hooks/useActiveRobotMap";
import { screenDragToCenterDelta, viewPanPixels } from "../services/gdmMap";
import {
  commitMapCenter,
  commitMapRotation,
  commitMapZoom,
  displayedCenter,
  displayedRotation,
  displayedZoom,
  MAP_CENTER_DEFAULT,
  MAP_ZOOM_DEFAULT,
  MAP_ZOOM_MAX,
  MAP_ZOOM_MIN,
  stageMapCenter,
  stageMapRotation,
  stageMapZoom,
} from "../services/mapRotation";
import {
  fetchRobotMaps,
  robotMapPreviewUrl,
  saveMapView,
  selectRobotMap,
} from "../services/robotMaps";
import "./MapSettingsPanel.css";

function formatWhen(updatedAt) {
  if (!updatedAt) return "";
  try {
    return new Date(updatedAt * 1000).toLocaleString("ko-KR", {
      month: "numeric",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return "";
  }
}

function formatSigned(value) {
  const rounded = Math.round(value * 10) / 10;
  return `${rounded > 0 ? "+" : ""}${rounded}`;
}

function formatOrigin(origin) {
  if (!Array.isArray(origin) || origin.length < 2) return "";
  const x = Number(origin[0]);
  const y = Number(origin[1]);
  if (!Number.isFinite(x) || !Number.isFinite(y)) return "";
  return `원점 ${x.toFixed(2)}, ${y.toFixed(2)}`;
}

export default function MapSettingsPanel() {
  const [status, setStatus] = useState("loading");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [folder, setFolder] = useState("");
  const [maps, setMaps] = useState([]);
  const [active, setActive] = useState(null);
  const [focus, setFocus] = useState(null);
  const [saving, setSaving] = useState(false);
  const [rotation, setRotation] = useState(0);
  const [zoom, setZoom] = useState(MAP_ZOOM_DEFAULT);
  const [center, setCenter] = useState({ x: MAP_CENTER_DEFAULT, y: MAP_CENTER_DEFAULT });
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const saveTimer = useRef(0);
  const pendingSave = useRef(null);
  const saveFlight = useRef(false);
  const previewImgRef = useRef(null);
  const centerDrag = useRef(null);

  const load = async () => {
    setStatus("loading");
    setError("");
    try {
      const payload = await fetchRobotMaps();
      const nextMaps = Array.isArray(payload.maps) ? payload.maps : [];
      setMaps(nextMaps);
      setFolder(payload.folder || "");
      setActive(payload.active ?? null);
      setFocus((current) => {
        if (current && nextMaps.some((item) => item.name === current)) return current;
        return payload.active ?? nextMaps[0]?.name ?? null;
      });
      setStatus("ok");
    } catch (err) {
      setStatus("error");
      setError(err instanceof Error ? err.message : "맵을 읽지 못했습니다");
    }
  };

  useEffect(() => {
    let cancelled = false;
    fetchRobotMaps()
      .then((payload) => {
        if (cancelled) return;
        const nextMaps = Array.isArray(payload.maps) ? payload.maps : [];
        setMaps(nextMaps);
        setFolder(payload.folder || "");
        setActive(payload.active ?? null);
        setFocus(payload.active ?? nextMaps[0]?.name ?? null);
        setStatus("ok");
      })
      .catch((err) => {
        if (cancelled) return;
        setStatus("error");
        setError(err instanceof Error ? err.message : "맵을 읽지 못했습니다");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const focused = maps.find((item) => item.name === focus) ?? null;

  useEffect(() => {
    if (!focused?.name) return;
    setRotation(displayedRotation(focused.name, focused.rotation));
    setZoom(displayedZoom(focused.name, focused.zoom));
    setCenter(displayedCenter(focused.name, focused.centerX, focused.centerY));
  }, [focused?.name, focused?.rotation, focused?.zoom, focused?.centerX, focused?.centerY]);

  useLayoutEffect(() => {
    const img = previewImgRef.current;
    if (!img) return undefined;
    const update = () => {
      setPan(viewPanPixels(img.offsetWidth, img.offsetHeight, rotation, zoom, center.x, center.y));
    };
    update();
    img.addEventListener("load", update);
    const observer = typeof ResizeObserver !== "undefined" ? new ResizeObserver(update) : null;
    observer?.observe(img);
    return () => {
      img.removeEventListener("load", update);
      observer?.disconnect();
    };
  }, [rotation, zoom, center.x, center.y, focused?.name, focused?.updatedAt]);

  useEffect(() => () => window.clearTimeout(saveTimer.current), []);

  const flushViewSave = () => {
    if (saveFlight.current) return;
    const job = pendingSave.current;
    if (!job) return;
    pendingSave.current = null;
    saveFlight.current = true;
    saveMapView(job.name, job.rotation, job.zoom, job.centerX, job.centerY)
      .then(() => {
        commitMapRotation(job.name, job.rotation, job.rotEpoch);
        commitMapZoom(job.name, job.zoom, job.zoomEpoch);
        commitMapCenter(job.name, job.centerX, job.centerY, job.centerEpoch);
      })
      .catch((err) => {
        setError(err instanceof Error ? err.message : "맵 보기를 저장하지 못했습니다");
      })
      .finally(() => {
        saveFlight.current = false;
        if (pendingSave.current) flushViewSave();
      });
  };

  const scheduleViewSave = (
    name,
    rotationValue,
    zoomValue,
    centerX,
    centerY,
    rotEpoch,
    zoomEpoch,
    centerEpoch,
  ) => {
    pendingSave.current = {
      name,
      rotation: rotationValue,
      zoom: zoomValue,
      centerX,
      centerY,
      rotEpoch,
      zoomEpoch,
      centerEpoch,
    };
    window.clearTimeout(saveTimer.current);
    saveTimer.current = window.setTimeout(() => flushViewSave(), 280);
  };

  const applyRotation = (value) => {
    if (!focused?.name) return;
    const name = focused.name;
    const { degrees, epoch } = stageMapRotation(name, value);
    const zoomEpoch = stageMapZoom(name, zoom).epoch;
    const centerEpoch = stageMapCenter(name, center.x, center.y).epoch;
    setRotation(degrees);
    setMaps((prev) =>
      prev.map((item) =>
        item.name === name
          ? { ...item, rotation: degrees, zoom, centerX: center.x, centerY: center.y }
          : item,
      ),
    );
    setError("");
    scheduleViewSave(name, degrees, zoom, center.x, center.y, epoch, zoomEpoch, centerEpoch);
  };

  const applyZoom = (value) => {
    if (!focused?.name) return;
    const name = focused.name;
    const { zoom: nextZoom, epoch } = stageMapZoom(name, value);
    const rotEpoch = stageMapRotation(name, rotation).epoch;
    const centerEpoch = stageMapCenter(name, center.x, center.y).epoch;
    setZoom(nextZoom);
    setMaps((prev) =>
      prev.map((item) =>
        item.name === name
          ? { ...item, rotation, zoom: nextZoom, centerX: center.x, centerY: center.y }
          : item,
      ),
    );
    setError("");
    scheduleViewSave(name, rotation, nextZoom, center.x, center.y, rotEpoch, epoch, centerEpoch);
  };

  const applyCenter = (x, y) => {
    if (!focused?.name) return;
    const name = focused.name;
    const next = stageMapCenter(name, x, y);
    const rotEpoch = stageMapRotation(name, rotation).epoch;
    const zoomEpoch = stageMapZoom(name, zoom).epoch;
    setCenter({ x: next.x, y: next.y });
    setMaps((prev) =>
      prev.map((item) =>
        item.name === name ? { ...item, rotation, zoom, centerX: next.x, centerY: next.y } : item,
      ),
    );
    setError("");
    scheduleViewSave(name, rotation, zoom, next.x, next.y, rotEpoch, zoomEpoch, next.epoch);
  };

  const onCenterPointerDown = (event) => {
    if (!focused?.name || event.button > 0) return;
    const img = previewImgRef.current;
    if (!img?.offsetWidth || !img.offsetHeight) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    centerDrag.current = {
      x: event.clientX,
      y: event.clientY,
      centerX: center.x,
      centerY: center.y,
      width: img.offsetWidth,
      height: img.offsetHeight,
    };
  };

  const onCenterPointerMove = (event) => {
    const drag = centerDrag.current;
    if (!drag) return;
    const delta = screenDragToCenterDelta(
      event.clientX - drag.x,
      event.clientY - drag.y,
      drag.width,
      drag.height,
      rotation,
      zoom,
    );
    applyCenter(drag.centerX + delta.x, drag.centerY + delta.y);
  };

  const onCenterPointerUp = () => {
    centerDrag.current = null;
  };
  const canChange = Boolean(
    focused?.pgm && focused?.yaml && focused.name !== active && !saving,
  );

  const changeMap = async () => {
    if (!focused || !canChange) return;
    setSaving(true);
    setNotice("");
    setError("");
    try {
      await selectRobotMap(focused.name);
      setActive(focused.name);
      setNotice("홈 화면 지도와 다음 주행에 이 맵을 사용합니다.");
      window.dispatchEvent(new CustomEvent(ROBOT_MAP_EVENT));
    } catch (err) {
      setError(err instanceof Error ? err.message : "맵을 바꾸지 못했습니다");
    } finally {
      setSaving(false);
    }
  };

  return (
    <section className="more-air-status-card more-tab-card more-map-card">
      <header className="more-air-status-header">
        <div className="more-map-icon" aria-hidden="true">
          <Map size={20} strokeWidth={2.4} />
        </div>
        <div className="more-air-status-copy">
          <p className="more-air-status-label">주행 맵</p>
          <h1>{focused?.name ?? "맵"}</h1>
          <p className="more-air-status-desc">
            보조선에 벽이 맞도록 돌리고, 100%를 기준으로 확대하거나 지도를 끌어 중심을 맞춥니다. 홈 화면에 바로 적용되고, 주행 젯슨에는 원래 맵 좌표로 보정해 전송합니다.
          </p>
        </div>
        <button
          type="button"
          className="more-map-refresh"
          onClick={() => void load()}
          disabled={status === "loading" || saving}
        >
          <RefreshCw size={16} strokeWidth={2.6} />
          <span>{status === "loading" ? "불러오는 중" : "다시 불러오기"}</span>
        </button>
      </header>

      {status === "loading" && maps.length === 0 ? (
        <p className="more-ip-empty">맵 파일을 읽는 중…</p>
      ) : null}
      {status === "error" ? (
        <p className="more-ip-empty is-error">{error || "맵을 읽지 못했습니다"}</p>
      ) : null}

      {focused?.pgm ? (
        <figure className="more-map-preview">
          <div
            className="more-map-rotate-stage"
            onPointerDown={onCenterPointerDown}
            onPointerMove={onCenterPointerMove}
            onPointerUp={onCenterPointerUp}
            onPointerCancel={onCenterPointerUp}
          >
            <img
              ref={previewImgRef}
              src={robotMapPreviewUrl(focused.name, focused.updatedAt)}
              alt={`${focused.name} 맵`}
              draggable={false}
              style={{
                transform: `translate(calc(-50% + ${pan.x}px), calc(-50% + ${pan.y}px)) rotate(${rotation}deg) scale(${zoom / 100})`,
              }}
            />
            <div className="more-map-guides" aria-hidden="true" />
          </div>
          <figcaption>
            {[
              focused.width && focused.height ? `${focused.width}×${focused.height}` : "",
              typeof focused.resolution === "number" ? `${focused.resolution} m` : "",
              formatOrigin(focused.origin),
              `${rotation > 0 ? "+" : ""}${rotation}°`,
              `${zoom}%`,
              `중심 ${formatSigned(center.x - 50)}, ${formatSigned(center.y - 50)}`,
              formatWhen(focused.updatedAt),
            ]
              .filter(Boolean)
              .join(" · ")}
          </figcaption>
        </figure>
      ) : focused ? (
        <p className="more-ip-empty">이 항목에는 볼 수 있는 이미지가 없습니다.</p>
      ) : null}

      {focused?.pgm ? (
        <div className="more-map-rotate">
          <div className="more-map-rotate-head">
            <span>라인 맞추기</span>
            <strong>{rotation > 0 ? `+${rotation}` : rotation}°</strong>
          </div>
          <div className="more-map-rotate-row">
            <button type="button" onClick={() => applyRotation(rotation - 90)} aria-label="반시계 90도">
              -90°
            </button>
            <button type="button" onClick={() => applyRotation(rotation - 1)} aria-label="반시계 1도">
              -1°
            </button>
            <input
              type="range"
              min={-180}
              max={180}
              step={1}
              value={rotation}
              aria-label="맵 회전 각도"
              onChange={(event) => applyRotation(Number(event.target.value))}
            />
            <button type="button" onClick={() => applyRotation(rotation + 1)} aria-label="시계 1도">
              +1°
            </button>
            <button type="button" onClick={() => applyRotation(rotation + 90)} aria-label="시계 90도">
              +90°
            </button>
          </div>
          <button
            type="button"
            className="more-map-rotate-reset"
            onClick={() => applyRotation(0)}
            disabled={rotation === 0}
          >
            회전 초기화
          </button>
          <div className="more-map-rotate-head">
            <span>확대</span>
            <strong>{zoom}%</strong>
          </div>
          <div className="more-map-rotate-row">
            <button type="button" onClick={() => applyZoom(zoom - 10)} aria-label="10퍼센트 축소">
              -10%
            </button>
            <input
              type="range"
              min={MAP_ZOOM_MIN}
              max={MAP_ZOOM_MAX}
              step={5}
              value={zoom}
              aria-label="맵 확대 퍼센트"
              onChange={(event) => applyZoom(Number(event.target.value))}
            />
            <button type="button" onClick={() => applyZoom(zoom + 10)} aria-label="10퍼센트 확대">
              +10%
            </button>
          </div>
          <button
            type="button"
            className="more-map-rotate-reset"
            onClick={() => applyZoom(MAP_ZOOM_DEFAULT)}
            disabled={zoom === MAP_ZOOM_DEFAULT}
          >
            100%로
          </button>
          <div className="more-map-rotate-head">
            <span>중심</span>
            <strong>
              {formatSigned(center.x - 50)}, {formatSigned(center.y - 50)}
            </strong>
          </div>
          <p className="more-map-center-hint">지도를 끌어 십자선에 맞춥니다.</p>
          <div className="more-map-center-pad">
            <button type="button" className="up" onClick={() => applyCenter(center.x, center.y + 2)}>
              위
            </button>
            <button type="button" className="left" onClick={() => applyCenter(center.x + 2, center.y)}>
              왼쪽
            </button>
            <button type="button" className="right" onClick={() => applyCenter(center.x - 2, center.y)}>
              오른쪽
            </button>
            <button type="button" className="down" onClick={() => applyCenter(center.x, center.y - 2)}>
              아래
            </button>
          </div>
          <button
            type="button"
            className="more-map-rotate-reset"
            onClick={() => applyCenter(MAP_CENTER_DEFAULT, MAP_CENTER_DEFAULT)}
            disabled={center.x === MAP_CENTER_DEFAULT && center.y === MAP_CENTER_DEFAULT}
          >
            중심 초기화
          </button>
        </div>
      ) : null}

      {status === "ok" && maps.length === 0 ? (
        <p className="more-ip-empty">폴더에 맵 파일이 없습니다.</p>
      ) : null}

      {maps.length > 0 ? (
        <div className="more-map-list" role="listbox" aria-label="맵 파일">
          {maps.map((item) => {
            const selected = item.name === focus;
            const inUse = item.name === active;
            return (
              <button
                key={item.name}
                type="button"
                role="option"
                aria-selected={selected}
                className={`more-map-item${selected ? " selected" : ""}${inUse ? " in-use" : ""}`}
                onClick={() => {
                  setFocus(item.name);
                  setNotice("");
                }}
              >
                {item.pgm ? (
                  <img
                    src={robotMapPreviewUrl(item.name, item.updatedAt)}
                    alt=""
                    style={{
                      transform: `rotate(${item.name === focus ? rotation : Number(item.rotation) || 0}deg) scale(${(item.name === focus ? zoom : Number(item.zoom) || MAP_ZOOM_DEFAULT) / 100})`,
                    }}
                  />
                ) : (
                  <span className="more-map-missing">이미지 없음</span>
                )}
                <span className="more-map-item-copy">
                  <strong>{item.name}</strong>
                  <span>{item.yaml && item.pgm ? "pgm · yaml" : item.pgm ? "pgm" : "yaml"}</span>
                </span>
                {inUse ? (
                  <span className="more-map-badge">
                    <Check size={14} strokeWidth={2.8} />
                    사용 중
                  </span>
                ) : null}
              </button>
            );
          })}
        </div>
      ) : null}

      <div className="more-map-actions">
        <button
          type="button"
          className="more-map-apply"
          onClick={() => void changeMap()}
          disabled={!canChange}
        >
          {saving ? "변경 중…" : focused?.name === active ? "사용 중인 맵" : "이 맵으로 변경"}
        </button>
      </div>
      {notice ? <p className="more-map-notice">{notice}</p> : null}
      {status === "ok" && error ? <p className="more-ip-empty is-error">{error}</p> : null}
      {folder ? <p className="more-map-folder">{folder}</p> : null}
    </section>
  );
}
