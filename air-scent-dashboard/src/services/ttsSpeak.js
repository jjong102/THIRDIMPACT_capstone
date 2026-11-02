const TTS_BASE = "/api/tts";

let speakController = null;

export async function fetchSpeakerVolume() {
  const response = await fetch(`${TTS_BASE}/volume`);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.error || "스피커 음량을 읽지 못했습니다");
  }
  return payload;
}

export async function setSpeakerVolume(volume) {
  const response = await fetch(`${TTS_BASE}/volume`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ volume }),
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.error || "스피커 음량을 바꾸지 못했습니다");
  }
  return payload;
}

export async function fetchTtsHealth() {
  const response = await fetch(`${TTS_BASE}/health`);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.error || "TTS health check failed");
  }
  return payload;
}

export async function fetchVoiceClips() {
  const response = await fetch(`${TTS_BASE}/clips`);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.error || "녹음 목록을 읽지 못했습니다");
  }
  return Array.isArray(payload.clips) ? payload.clips : [];
}

export async function playVoiceClip(file) {
  const name = String(file || "").trim();
  if (!name) {
    return { ok: false, error: "재생할 파일이 없습니다" };
  }

  speakController?.abort();
  const controller = new AbortController();
  speakController = controller;

  try {
    const response = await fetch(`${TTS_BASE}/clip`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ file: name }),
      signal: controller.signal,
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      return { ok: false, error: payload.error || "음성 재생에 실패했습니다" };
    }
    return payload;
  } catch (err) {
    if (err?.name === "AbortError") {
      return { ok: true, skipped: true, reason: "aborted" };
    }
    return { ok: false, error: err?.message || "음성 재생에 실패했습니다" };
  }
}

export async function speakText(text) {
  const trimmed = String(text || "").trim();
  if (!trimmed) {
    return { ok: true, skipped: true };
  }

  speakController?.abort();
  const controller = new AbortController();
  speakController = controller;

  try {
    const response = await fetch(`${TTS_BASE}/speak`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: trimmed }),
      signal: controller.signal,
    });

    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      return { ok: false, error: payload.error || "TTS 요청 실패" };
    }
    return payload;
  } catch (err) {
    if (err?.name === "AbortError") {
      return { ok: true, skipped: true, reason: "aborted" };
    }
    return { ok: false, error: err?.message || "TTS 요청 실패" };
  }
}

export async function stopSpeaking() {
  speakController?.abort();
  speakController = null;

  if (typeof window !== "undefined") {
    window.speechSynthesis?.cancel();
  }

  try {
    await fetch(`${TTS_BASE}/stop`, { method: "POST" });
  } catch {
    // 브리지가 없어도 마이크 입력은 계속 진행한다
  }
}
