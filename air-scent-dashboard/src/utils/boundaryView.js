import { orientedRectToMapPolygon } from "../services/gdmMap";

/**
 * 전체화면에서 한계구역이 화면에 꽉 차게 하는 보기 값 (11/15, 보기만 — 좌표·기능은 같은 계산을 따른다).
 * computeContainLayout 과 같은 식(맞춤 배율 → 회전 맞춤 배율 → 확대)을 거꾸로 풀어 zoom·center 를 구한다.
 * 반환: { zoom, centerX, centerY } (computeContainLayout 인자) 또는 null
 */
export function boundaryFitView(boundary, mapMeta, naturalW, naturalH, containerW, containerH, rotationDeg, margin = 0.9) {
  if (!boundary || !mapMeta || !naturalW || !naturalH || !containerW || !containerH) return null;
  const poly = orientedRectToMapPolygon(boundary, mapMeta);
  if (!poly) return null;

  const scale = Math.min(containerW / naturalW, containerH / naturalH);
  const width = naturalW * scale;
  const height = naturalH * scale;
  const rad = ((Number(rotationDeg) || 0) * Math.PI) / 180;
  const cos = Math.cos(rad);
  const sin = Math.sin(rad);

  // 맞춤 상자 안 px, 상자 가운데 기준으로 회전했을 때 차지하는 폭·높이 (배율 1)
  const pts = poly.map(([x, y]) => {
    const dx = (x / 100) * width - width / 2;
    const dy = (y / 100) * height - height / 2;
    return [dx * cos - dy * sin, dx * sin + dy * cos];
  });
  const xs = pts.map((p) => p[0]);
  const ys = pts.map((p) => p[1]);
  const spanW = Math.max(...xs) - Math.min(...xs);
  const spanH = Math.max(...ys) - Math.min(...ys);
  if (spanW <= 0 || spanH <= 0) return null;

  const boundW = width * Math.abs(cos) + height * Math.abs(sin);
  const boundH = width * Math.abs(sin) + height * Math.abs(cos);
  const rotationScale = Math.min(1, containerW / boundW, containerH / boundH);
  const viewScale = Math.min((containerW * margin) / spanW, (containerH * margin) / spanH);

  const centerX = poly.reduce((s, p) => s + p[0], 0) / poly.length;
  const centerY = poly.reduce((s, p) => s + p[1], 0) / poly.length;
  return {
    zoom: (viewScale / rotationScale) * 100,
    centerX: Math.min(100, Math.max(0, centerX)),
    centerY: Math.min(100, Math.max(0, centerY)),
  };
}
