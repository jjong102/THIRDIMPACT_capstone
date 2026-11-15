const API_BASE = import.meta.env.VITE_SYSTEM_API ?? "";

export async function fetchSystemHosts() {
  const response = await fetch(`${API_BASE}/api/system/hosts`);
  const payload = await response.json().catch(() => ({}));

  if (!response.ok || payload.ok === false) {
    throw new Error(payload.error || `기기 조회 실패 (${response.status})`);
  }

  return Array.isArray(payload.hosts) ? payload.hosts : [];
}

export async function fetchWifiStatus() {
  const response = await fetch(`${API_BASE}/api/system/wifi`);
  const payload = await response.json().catch(() => ({}));

  if (!response.ok || payload.ok === false) {
    throw new Error(payload.error || `Wi-Fi 조회 실패 (${response.status})`);
  }

  return payload;
}

export async function requestSystemPower(action) {
  const response = await fetch(`${API_BASE}/api/system/${action}`, {
    method: "POST",
  });

  const payload = await response.json().catch(() => ({}));

  if (!response.ok || payload.ok === false) {
    throw new Error(payload.error || `power ${action} failed (${response.status})`);
  }

  return payload;
}
