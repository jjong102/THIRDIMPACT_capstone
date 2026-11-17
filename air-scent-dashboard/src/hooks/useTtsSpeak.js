import { useCallback, useEffect, useRef, useState } from "react";

import { speakText, stopSpeaking } from "../services/ttsSpeak";

export default function useTtsSpeak() {
  const [speaking, setSpeaking] = useState(false);
  const aliveRef = useRef(true);
  const generationRef = useRef(0);
  const retainRef = useRef(false);

  useEffect(() => {
    aliveRef.current = true;

    return () => {
      aliveRef.current = false;
      generationRef.current += 1;
      // 향 설정 안내는 창이 닫힌 뒤에도 스피커에서 끝까지 재생한다.
      if (!retainRef.current) {
        stopSpeaking();
      }
    };
  }, []);

  const speak = useCallback((text) => {
    const trimmed = String(text || "").trim();
    if (!trimmed || !aliveRef.current) {
      return;
    }

    setSpeaking(true);
    speakText(trimmed).catch(() => {});
  }, []);

  const stop = useCallback(() => {
    generationRef.current += 1;
    setSpeaking(false);
    return stopSpeaking();
  }, []);

  const retain = useCallback(() => {
    retainRef.current = true;
  }, []);

  const release = useCallback(() => {
    retainRef.current = false;
  }, []);

  return { speaking, speak, stop, retain, release };
}
