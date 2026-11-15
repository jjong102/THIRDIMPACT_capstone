const API_BASE = import.meta.env.VITE_SYSTEM_API ?? "";

export async function fetchRobotMaps() {
  const response = await fetch(`${API_BASE}/api/maps`);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok || payload.ok === false) {
    throw new Error(payload.error || "맵 목록을 읽지 못했습니다");
  }
  return payload;
}

export async function saveMapView(name, degrees, zoom, centerX = 50, centerY = 50) {
  const response = await fetch(`${API_BASE}/api/maps/view`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, degrees, zoom, centerX, centerY }),
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok || payload.ok === false) {
    throw new Error(payload.error || "맵 보기를 저장하지 못했습니다");
  }
  return payload;
}

export async function saveMapRotation(name, degrees) {
  const response = await fetch(`${API_BASE}/api/maps/rotation`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, degrees }),
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok || payload.ok === false) {
    throw new Error(payload.error || "맵 회전을 저장하지 못했습니다");
  }
  return payload;
}

export async function selectRobotMap(name) {
  const response = await fetch(`${API_BASE}/api/maps/select`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok || payload.ok === false) {
    throw new Error(payload.error || "맵을 바꾸지 못했습니다");
  }
  return payload;
}

export function robotMapPreviewUrl(name, updatedAt) {
  const params = new URLSearchParams({ name, v: "lines2" });
  if (updatedAt) params.set("t", String(updatedAt));
  return `${API_BASE}/api/maps/preview?${params.toString()}`;
}
