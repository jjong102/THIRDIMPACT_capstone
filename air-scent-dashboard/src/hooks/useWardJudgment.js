import { useEffect, useState } from "react";

const API_BASE = import.meta.env.VITE_GDM_API ?? "";
const POLL_MS = 2000;
const STALE_SEC = 60; // 와드는 약 10초마다 보냄. 60초 넘게 없으면 없는 것으로

/**
 * 지금 와드 판정 (UI Jetson 서버 ward_dispatch 가 화면 상태 wardState 에 넣는 것).
 * 60초 넘게 새 판정이 없으면 null.
 */
export default function useWardJudgment() {
  const [ward, setWard] = useState(null);
  const [now, setNow] = useState(() => Date.now() / 1000);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const response = await fetch(`${API_BASE}/api/nav/ui`);
        const payload = response.ok ? await response.json() : null;
        if (!cancelled) setWard(payload?.state?.wardState ?? null);
      } catch {
        // 직전 값을 유지한다
      }
      if (!cancelled) setNow(Date.now() / 1000);
    };
    load();
    const timer = window.setInterval(load, POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, []);

  const age = ward?.rx_time ? now - ward.rx_time : null;
  return age != null && age <= STALE_SEC ? ward : null;
}
