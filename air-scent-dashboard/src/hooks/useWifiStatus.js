import { useEffect, useState } from "react";

import { fetchWifiStatus } from "../services/systemPower";

const EMPTY = {
  connected: false,
  ssid: null,
  dbm: null,
  level: 0,
  error: null,
};

export function useWifiStatus(pollMs = 4000) {
  const [status, setStatus] = useState(EMPTY);

  useEffect(() => {
    let cancelled = false;

    const load = async () => {
      try {
        const payload = await fetchWifiStatus();
        if (cancelled) return;
        setStatus({
          connected: Boolean(payload.connected),
          ssid: payload.ssid ?? null,
          dbm: typeof payload.dbm === "number" ? payload.dbm : null,
          level: Number(payload.level) || 0,
          error: null,
        });
      } catch (error) {
        if (cancelled) return;
        setStatus({ ...EMPTY, error: error.message });
      }
    };

    load();
    const timer = window.setInterval(load, pollMs);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [pollMs]);

  return status;
}
