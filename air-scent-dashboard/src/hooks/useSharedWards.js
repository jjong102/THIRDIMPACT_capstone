import { useEffect, useRef, useState } from "react";

import { fetchMoveUi } from "../services/moveUi";
import { loadWards, MAX_WARDS, saveWards, wardDisplayName } from "../utils/wardStorage";

const POLL_MS = 1000;

function sameWards(a, b) {
  if (a === b) return true;
  if (!Array.isArray(a) || !Array.isArray(b) || a.length !== b.length) {
    return false;
  }
  return a.every(
    (ward, index) =>
      ward.id === b[index].id &&
      ward.name === b[index].name &&
      Number(ward.x) === Number(b[index].x) &&
      Number(ward.y) === Number(b[index].y)
  );
}

export default function useSharedWards() {
  const [wards, setWards] = useState(loadWards);
  const wardsRef = useRef(wards);
  wardsRef.current = wards;

  useEffect(() => {
    let cancelled = false;

    const apply = (list) => {
      if (!Array.isArray(list) || list.length === 0) return;
      const next = list.slice(0, MAX_WARDS).map((ward) => ({
        ...ward,
        name: wardDisplayName(ward),
      }));
      if (sameWards(next, wardsRef.current)) return;
      saveWards(next);
      setWards(next);
    };

    async function tick() {
      try {
        const state = await fetchMoveUi();
        if (!cancelled && state?.wards?.length) {
          apply(state.wards);
        }
      } catch {
        if (!cancelled) {
          setWards((prev) => {
            const next = loadWards();
            return sameWards(prev, next) ? prev : next;
          });
        }
      }
    }

    tick();
    const timer = window.setInterval(tick, POLL_MS);
    const onLocal = () => {
      if (!cancelled) setWards(loadWards());
    };
    window.addEventListener("focus", onLocal);
    window.addEventListener("air-scent-wards-changed", onLocal);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
      window.removeEventListener("focus", onLocal);
      window.removeEventListener("air-scent-wards-changed", onLocal);
    };
  }, []);

  return wards;
}
