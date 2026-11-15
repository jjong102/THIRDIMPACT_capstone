import { useEffect, useState } from "react";

const API_BASE = import.meta.env.VITE_GDM_API ?? "";
const POLL_MS = 1500;

/** 로봇 위치 추정 일치율 (앞·뒤 LiDAR 벽 경계). 서버가 10초 넘게 못 받으면 숨긴다. */
export default function LocMatchChip() {
  const [q, setQ] = useState(null);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const response = await fetch(`${API_BASE}/api/nav/ui`);
        const payload = response.ok ? await response.json() : null;
        if (!cancelled) setQ(payload?.state?.localizationQuality ?? null);
      } catch {
        if (!cancelled) setQ(null);
      }
    };
    load();
    const timer = window.setInterval(load, POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, []);

  if (!q || (q.front == null && q.rear == null)) return null;

  const need = q.need ?? 70;
  const tone = q.valid ? "ok" : Math.min(q.front ?? 0, q.rear ?? 0) >= need ? "near" : "low";
  const part = (label, value) => (value == null ? null : `${label} ${value}%`);
  const text = [part("앞", q.front), part("뒤", q.rear)].filter(Boolean).join(" · ");
  const title = [
    `벽 경계 일치율 (기준 ${need}% 이상)`,
    q.err_cm != null ? `벽 오차 ${q.err_cm}cm` : null,
    q.state ? `상태 ${q.state}` : null,
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <span className={`slam-loc-match is-${tone}`} title={title}>
      일치율 {text}
    </span>
  );
}
