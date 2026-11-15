import { sendFragranceCommands } from "./fragranceSerial";

const API_BASE = import.meta.env.VITE_GDM_API ?? "";

export async function fetchMoveUi() {
  const response = await fetch(`${API_BASE}/api/nav/ui`);
  if (!response.ok) {
    throw new Error(`move ui failed (${response.status})`);
  }
  const payload = await response.json().catch(() => ({}));
  return payload?.state ?? null;
}

export async function putMoveUi(state) {
  const response = await fetch(`${API_BASE}/api/nav/ui`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ state }),
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok || !payload?.ok) {
    throw new Error(payload?.error || `move ui put failed (${response.status})`);
  }
  return payload;
}

export async function setSharedFragranceSpraying(wardId) {
  return putMoveUi({
    fragranceSpraying: true,
    fragranceWardId: wardId ?? null,
  });
}

export async function clearSharedFragranceSpraying() {
  return putMoveUi({
    fragranceSpraying: false,
    fragranceWardId: null,
  });
}

export async function stopSharedFragranceSpray() {
  try {
    await clearSharedFragranceSpraying();
  } catch {
    // keep trying the Arduino off command
  }

  // 서버가 멈춘다: 도착 후 청정·발향 순서가 돌면 그 순서를 멈추고 전부 끄고, 아니면 분무만 끈다
  try {
    const response = await fetch(`${API_BASE}/api/nav/fragrance/stop`, { method: "POST" });
    if (response.ok) return;
  } catch {
    // 아래에서 아두이노에 직접 보낸다
  }

  try {
    await sendFragranceCommands(["MIST OFF"], {
      fragranceOn: false,
      fragranceDiffusing: false,
      fragranceChannels: { mint: false, lavender: false, orange: false },
    });
  } catch {
    // UI already cleared; Arduino hook retries on the robot
  }
}
