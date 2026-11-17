import { useEffect, useRef, useState } from "react";

import { fetchPatrolZones, orientedRectToMapPolygon } from "../services/gdmMap";

/*
 * 지도 보기 전용 다듬기 (11/15). 원본 지도 파일·좌표·기능은 그대로 두고, 브라우저에서 사본만 그린다.
 *  - 한계구역 안: 벽 윤곽(이중선·점선)을 메워 가운데 선으로 줄이고(세선화) 매끈한 선으로 다시 그림
 *  - 한계구역 밖: 흐리고 연하게
 * 같은 <img> 클래스(slam-map-image)를 써서 위치·회전·확대가 원본과 똑같이 따라간다.
 * 구역이 없거나 실패하면 아무것도 그리지 않아 원본이 그대로 보인다.
 */
const SCALE = 4;            // 출력 해상도 배수 (선이 확대돼도 매끈하게)
const CLOSE_RADIUS = 5;     // 이중선·끊긴 점 메우기 (11px 창 ≈ 33cm)
const JOIN_GAP = 10;        // 이 거리(px) 안의 선 끝은 잇기
const SIMPLIFY_EPS = 4;     // 선 단순화 허용 오차(px) — 직각으로 바꾸기 전에 크게 펴 둔다
const MIN_STEP = 4;         // 직각 선에서 이보다 짧은 꺾임은 펴기(px)
const LINE_WIDTH = 2.2;     // 원본 px 기준 선 굵기
const WALL_COLOR = "#1e293b";
const OUTSIDE_COLOR = "rgba(100, 116, 139, 1)";
const OUTSIDE_ALPHA = 0.55;
const OUTSIDE_BLUR = 2.2;   // 원본 px 기준

function loadImage(src) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = reject;
    img.src = src;
  });
}

/** 정사각 창 최대(dilate)/최소(erode) — 가로·세로 나눠서 */
function morph(src, w, h, r, isMax) {
  const tmp = new Uint8Array(w * h);
  const out = new Uint8Array(w * h);
  const pick = isMax ? 1 : 0;
  for (let y = 0; y < h; y += 1) {
    for (let x = 0; x < w; x += 1) {
      let v = isMax ? 0 : 1;
      for (let k = Math.max(0, x - r); k <= Math.min(w - 1, x + r); k += 1) {
        if (src[y * w + k] === pick) { v = pick; break; }
      }
      tmp[y * w + x] = v;
    }
  }
  for (let x = 0; x < w; x += 1) {
    for (let y = 0; y < h; y += 1) {
      let v = isMax ? 0 : 1;
      for (let k = Math.max(0, y - r); k <= Math.min(h - 1, y + r); k += 1) {
        if (tmp[k * w + x] === pick) { v = pick; break; }
      }
      out[y * w + x] = v;
    }
  }
  return out;
}

/** Zhang-Suen 세선화 → 1px 가운데 선 */
function thin(img, w, h) {
  const a = img.slice();
  const at = (x, y) => (x < 0 || y < 0 || x >= w || y >= h ? 0 : a[y * w + x]);
  let changed = true;
  while (changed) {
    changed = false;
    for (let step = 0; step < 2; step += 1) {
      const remove = [];
      for (let y = 0; y < h; y += 1) {
        for (let x = 0; x < w; x += 1) {
          if (!a[y * w + x]) continue;
          const p = [at(x, y - 1), at(x + 1, y - 1), at(x + 1, y), at(x + 1, y + 1),
            at(x, y + 1), at(x - 1, y + 1), at(x - 1, y), at(x - 1, y - 1)];
          const b = p.reduce((s, v) => s + v, 0);
          if (b < 2 || b > 6) continue;
          let transitions = 0;
          for (let i = 0; i < 8; i += 1) if (!p[i] && p[(i + 1) % 8]) transitions += 1;
          if (transitions !== 1) continue;
          const [p2, , p4, , p6, , p8] = p;
          const ok = step === 0
            ? p2 * p4 * p6 === 0 && p4 * p6 * p8 === 0
            : p2 * p4 * p8 === 0 && p2 * p6 * p8 === 0;
          if (ok) remove.push(y * w + x);
        }
      }
      if (remove.length) {
        changed = true;
        for (const i of remove) a[i] = 0;
      }
    }
  }
  return a;
}

/** 세선 픽셀 → 선(점 목록)들. 끝점·갈림점에서 끊고, 남은 고리도 */
function tracePaths(sk, w, h) {
  const N8 = [[-1, -1], [0, -1], [1, -1], [-1, 0], [1, 0], [-1, 1], [0, 1], [1, 1]];
  const on = (x, y) => x >= 0 && y >= 0 && x < w && y < h && sk[y * w + x] === 1;
  const nbrs = (i) => {
    const x = i % w; const y = (i - x) / w;
    const out = [];
    for (const [dx, dy] of N8) if (on(x + dx, y + dy)) out.push((y + dy) * w + x + dx);
    return out;
  };
  const seen = new Set();
  const key = (i, j) => (i < j ? `${i},${j}` : `${j},${i}`);
  const paths = [];
  const walk = (start, next) => {
    const path = [start, next];
    seen.add(key(start, next));
    let prev = start; let cur = next;
    for (;;) {
      const nb = nbrs(cur);
      if (nb.length !== 2) break;
      const nx = nb.find((q) => q !== prev && !seen.has(key(cur, q)));
      if (nx === undefined) break;
      seen.add(key(cur, nx));
      path.push(nx);
      prev = cur; cur = nx;
    }
    return path;
  };
  const pixels = [];
  for (let i = 0; i < sk.length; i += 1) if (sk[i]) pixels.push(i);
  const order = pixels.filter((i) => nbrs(i).length !== 2).concat(pixels);
  for (const p of order) {
    for (const q of nbrs(p)) if (!seen.has(key(p, q))) paths.push(walk(p, q));
  }
  return paths
    .filter((p) => p.length >= 3)
    .map((p) => p.map((i) => [i % w, Math.floor(i / w)]));
}

function simplify(points, eps) {
  if (points.length < 3) return points;
  const [ax, ay] = points[0];
  const [bx, by] = points[points.length - 1];
  const len = Math.hypot(bx - ax, by - ay) || 1e-9;
  let best = -1; let bestD = 0;
  for (let i = 1; i < points.length - 1; i += 1) {
    const [px, py] = points[i];
    const d = Math.abs((bx - ax) * (py - ay) - (by - ay) * (px - ax)) / len;
    if (d > bestD) { bestD = d; best = i; }
  }
  if (bestD <= eps) return [points[0], points[points.length - 1]];
  return simplify(points.slice(0, best + 1), eps).slice(0, -1).concat(simplify(points.slice(best), eps));
}

/**
 * 한계구역 사각형 방향(가로·세로)으로만 꺾이는 선으로 바꾼다 — 실제 맵과 조금 달라도 깔끔하게 (11/15).
 * 점들을 구역 축 좌표(u, v)로 돌려 각 구간을 긴 축 방향 하나로 붙이고, 같은 방향은 합치고, 짧은 꺾임은 편다.
 */
function makeAxes(poly) {
  const ang = Math.atan2(poly[1][1] - poly[0][1], poly[1][0] - poly[0][0]);
  const c = Math.cos(ang); const s = Math.sin(ang);
  return {
    toLocal: ([x, y]) => [x * c + y * s, -x * s + y * c],
    toWorld: ([u, v]) => [u * c - v * s, u * s + v * c],
  };
}

function cleanOrtho(points) {
  // 같은 축으로 이어지는 점 합치기
  let pts = points.slice();
  let changed = true;
  while (changed && pts.length > 2) {
    changed = false;
    for (let i = 1; i < pts.length - 1; i += 1) {
      const [a, b, c] = [pts[i - 1], pts[i], pts[i + 1]];
      const sameU = Math.abs(a[0] - b[0]) < 0.01 && Math.abs(b[0] - c[0]) < 0.01;
      const sameV = Math.abs(a[1] - b[1]) < 0.01 && Math.abs(b[1] - c[1]) < 0.01;
      if (sameU || sameV) { pts.splice(i, 1); changed = true; break; }
    }
    // 짧은 꺾임(계단) 펴기: a→b 가 짧고 앞뒤가 같은 방향이면 b 를 앞 선에 맞춘다
    for (let i = 1; !changed && i < pts.length - 2; i += 1) {
      const [a, b, c, d] = [pts[i - 1], pts[i], pts[i + 1], pts[i + 2]];
      const len = Math.hypot(c[0] - b[0], c[1] - b[1]);
      const abH = Math.abs(a[1] - b[1]) < 0.01; const cdH = Math.abs(c[1] - d[1]) < 0.01;
      const abV = Math.abs(a[0] - b[0]) < 0.01; const cdV = Math.abs(c[0] - d[0]) < 0.01;
      if (len < MIN_STEP && ((abH && cdH) || (abV && cdV))) {
        if (abH) { c[1] = b[1]; d[1] = b[1]; } else { c[0] = b[0]; d[0] = b[0]; }
        pts.splice(i, 1);
        changed = true;
      }
    }
  }
  return pts;
}

const MIN_RUN = 6;          // 이보다 짧은 가로·세로 토막은 이웃 선에 합치기(px)

/**
 * 직각 선 만들기 (계단 없이): 같은 방향으로 이어지는 구간을 한 토막(run)으로 묶어
 * 그 토막의 평균 위치에 곧은 선 하나로 두고, 이웃 토막과는 직각 모서리로 잇는다.
 * 살짝 기운 긴 벽도 계단이 아니라 곧은 선 하나가 된다.
 */
function orthogonalize(path, axes) {
  const pts = path.map((p) => axes.toLocal(p));
  if (pts.length < 2) return path;
  // 1) 구간마다 방향(H 가로 / V 세로)과 길이
  let runs = [];
  for (let i = 0; i < pts.length - 1; i += 1) {
    const [a, b] = [pts[i], pts[i + 1]];
    const du = b[0] - a[0]; const dv = b[1] - a[1];
    const horizontal = Math.abs(du) >= Math.abs(dv);
    const len = Math.hypot(du, dv);
    if (len < 1e-6) continue;
    const last = runs[runs.length - 1];
    if (last && last.horizontal === horizontal) {
      last.points.push(b); last.len += len;
    } else {
      runs.push({ horizontal, points: [a, b], len });
    }
  }
  if (!runs.length) return path;
  // 2) 짧은 토막은 지우고 앞뒤 같은 방향 토막을 합친다
  let changed = true;
  while (changed && runs.length > 1) {
    changed = false;
    for (let i = 0; i < runs.length; i += 1) {
      if (runs[i].len >= MIN_RUN) continue;
      const prev = runs[i - 1]; const next = runs[i + 1];
      if (prev && next && prev.horizontal === next.horizontal) {
        prev.points = prev.points.concat(runs[i].points, next.points);
        prev.len += runs[i].len + next.len;
        runs.splice(i, 2);
      } else if (prev || next) {
        const keep = prev || next;
        keep.points = prev ? keep.points.concat(runs[i].points) : runs[i].points.concat(keep.points);
        keep.len += runs[i].len;
        runs.splice(i, 1);
      } else {
        continue;
      }
      changed = true;
      break;
    }
  }
  // 3) 토막마다 곧은 선의 위치(가로면 v, 세로면 u) = 점들 평균, 범위 = 처음·끝
  const lines = runs.map((r) => {
    const axis = r.horizontal ? 1 : 0;
    const level = r.points.reduce((s, p) => s + p[axis], 0) / r.points.length;
    return { ...r, level };
  });
  const out = [];
  const first = lines[0]; const lastRun = lines[lines.length - 1];
  const startPt = first.points[0];
  out.push(first.horizontal ? [startPt[0], first.level] : [first.level, startPt[1]]);
  for (let i = 0; i < lines.length - 1; i += 1) {
    const a = lines[i]; const b = lines[i + 1];
    // 가로(v=a.level) 와 세로(u=b.level) 가 만나는 점 (또는 그 반대)
    out.push(a.horizontal ? [b.level, a.level] : [a.level, b.level]);
  }
  const endPt = lastRun.points[lastRun.points.length - 1];
  out.push(lastRun.horizontal ? [endPt[0], lastRun.level] : [lastRun.level, endPt[1]]);
  return out.map((p) => axes.toWorld(p));
}

function orthoBridge(p, q, axes) {
  const a = axes.toLocal(p); const b = axes.toLocal(q);
  return [a, [b[0], a[1]], b].map((x) => axes.toWorld(x));
}

function insideMask(poly, w, h) {
  const c = document.createElement("canvas");
  c.width = w; c.height = h;
  const g = c.getContext("2d");
  g.beginPath();
  poly.forEach(([x, y], i) => (i ? g.lineTo(x, y) : g.moveTo(x, y)));
  g.closePath();
  g.fillStyle = "#000";
  g.fill();
  const data = g.getImageData(0, 0, w, h).data;
  const m = new Uint8Array(w * h);
  for (let i = 0; i < m.length; i += 1) m[i] = data[i * 4 + 3] > 127 ? 1 : 0;
  return m;
}

/** 원본 이미지 + 한계구역(이미지 px) → 다듬은 그림 canvas */
function renderClean(img, poly) {
  const w = img.naturalWidth; const h = img.naturalHeight;
  const base = document.createElement("canvas");
  base.width = w; base.height = h;
  const bg = base.getContext("2d");
  bg.drawImage(img, 0, 0);
  const px = bg.getImageData(0, 0, w, h).data;
  const lines = new Uint8Array(w * h);
  for (let i = 0; i < lines.length; i += 1) {
    const a = px[i * 4 + 3];
    const lum = (px[i * 4] + px[i * 4 + 1] + px[i * 4 + 2]) / 3;
    lines[i] = a > 40 && lum < 160 ? 1 : 0;   // 선(어두운·불투명) 픽셀
  }
  const inside = insideMask(poly, w, h);

  // 안: 메우기(팽창→침식) → 구역 안만 → 세선화 → 선 따라가기 → 단순화 → 가까운 끝 잇기
  const closed = morph(morph(lines, w, h, CLOSE_RADIUS, true), w, h, CLOSE_RADIUS, false);
  const body = new Uint8Array(w * h);
  for (let i = 0; i < body.length; i += 1) body[i] = (closed[i] || lines[i]) && inside[i] ? 1 : 0;
  const axes = makeAxes(poly);
  const raw = tracePaths(thin(body, w, h), w, h).map((p) => simplify(p, SIMPLIFY_EPS));
  const paths = raw.map((p) => orthogonalize(p, axes));
  const ends = [];
  paths.forEach((p, i) => { ends.push([i, p[0]]); ends.push([i, p[p.length - 1]]); });
  for (let a = 0; a < ends.length; a += 1) {
    for (let b = a + 1; b < ends.length; b += 1) {
      const [i, p] = ends[a]; const [j, q] = ends[b];
      if (i !== j && Math.hypot(p[0] - q[0], p[1] - q[1]) <= JOIN_GAP) paths.push(orthoBridge(p, q, axes));
    }
  }

  const out = document.createElement("canvas");
  out.width = w * SCALE; out.height = h * SCALE;
  const g = out.getContext("2d");

  // 밖: 원본 선을 흐리고 연하게 (구역 밖만)
  const outsideLayer = document.createElement("canvas");
  outsideLayer.width = w; outsideLayer.height = h;
  const og = outsideLayer.getContext("2d");
  const od = og.createImageData(w, h);
  for (let i = 0; i < lines.length; i += 1) {
    if (lines[i] && !inside[i]) { od.data[i * 4] = 100; od.data[i * 4 + 1] = 116; od.data[i * 4 + 2] = 139; od.data[i * 4 + 3] = 255; }
  }
  og.putImageData(od, 0, 0);
  g.save();
  g.globalAlpha = OUTSIDE_ALPHA;
  g.filter = `blur(${OUTSIDE_BLUR * SCALE}px)`;
  g.imageSmoothingEnabled = true;
  g.drawImage(outsideLayer, 0, 0, w * SCALE, h * SCALE);
  g.restore();

  // 안: 매끈한 선
  g.save();
  g.strokeStyle = WALL_COLOR;
  g.fillStyle = WALL_COLOR;
  g.lineWidth = LINE_WIDTH * SCALE;
  g.lineCap = "square";   // 직각 도면처럼 각진 끝
  g.lineJoin = "miter";
  for (const path of paths) {
    g.beginPath();
    path.forEach(([x, y], i) => {
      const X = (x + 0.5) * SCALE; const Y = (y + 0.5) * SCALE;
      if (i) g.lineTo(X, Y); else g.moveTo(X, Y);
    });
    if (path.length === 1) g.lineTo((path[0][0] + 0.5) * SCALE + 0.1, (path[0][1] + 0.5) * SCALE);
    g.stroke();
  }
  g.restore();
  return out;
}

export default function CleanMapLayer({ src, mapMeta, ready }) {
  const holderRef = useRef(null);
  const [boundaryKey, setBoundaryKey] = useState(null);
  const [zones, setZones] = useState(null);
  const [done, setDone] = useState(false);

  // 한계구역은 가끔만 바뀐다 — 10초마다 확인, 바뀌었을 때만 다시 그림
  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      try {
        const next = await fetchPatrolZones();
        if (cancelled || !next?.boundary) return;
        const key = JSON.stringify(next.boundary);
        setBoundaryKey((prev) => (prev === key ? prev : key));
        setZones(next);
      } catch {
        // 직전 구역 유지
      }
    };
    tick();
    const timer = window.setInterval(tick, 10000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, []);

  useEffect(() => {
    setDone(false);
    const holder = holderRef.current;
    if (holder) holder.replaceChildren();
    if (!ready || !src || !zones?.boundary || mapMeta?.source !== "robot") return undefined;
    let cancelled = false;
    (async () => {
      try {
        const img = await loadImage(src);
        const percent = orientedRectToMapPolygon(zones.boundary, mapMeta);
        if (!percent || cancelled) return;
        const poly = percent.map(([x, y]) => [(x / 100) * img.naturalWidth, (y / 100) * img.naturalHeight]);
        // 무거운 계산은 그림 그리기를 막지 않게 다음 틈에
        await new Promise((r) => window.setTimeout(r, 30));
        if (cancelled) return;
        const canvas = renderClean(img, poly);
        if (cancelled || !holderRef.current) return;
        canvas.className = "slam-map-image ready slam-map-clean";
        canvas.setAttribute("aria-hidden", "true");
        holderRef.current.replaceChildren(canvas);
        setDone(true);
      } catch (error) {
        console.warn("[clean-map] 원본 지도를 그대로 씁니다:", error);
      }
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [src, ready, boundaryKey, mapMeta?.source, mapMeta?.resolution, JSON.stringify(mapMeta?.origin), JSON.stringify(mapMeta?.image_size)]);

  return <div ref={holderRef} className={`slam-map-clean-holder${done ? " is-ready" : ""}`} aria-hidden="true" />;
}
