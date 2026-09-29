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

  try {
    await sendFragranceCommands(["M000"], {
      fragranceOn: false,
      fragranceDiffusing: false,
      fragranceChannels: { musk: false, lavender: false, woody: false },
    });
  } catch {
    // UI already cleared; Arduino hook retries on the robot
  }
}
