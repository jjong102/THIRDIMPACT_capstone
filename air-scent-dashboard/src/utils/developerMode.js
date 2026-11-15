const STORAGE_KEY = "air-scent-developer-mode";
const CHANGE_EVENT = "air-scent-developer-mode-changed";

export function loadDeveloperMode() {
  try {
    return window.localStorage.getItem(STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

export function saveDeveloperMode(enabled) {
  try {
    window.localStorage.setItem(STORAGE_KEY, enabled ? "1" : "0");
  } catch {
    // ignore storage errors
  }
  window.dispatchEvent(new Event(CHANGE_EVENT));
}

export function subscribeDeveloperMode(onChange) {
  const notify = () => onChange(loadDeveloperMode());
  window.addEventListener(CHANGE_EVENT, notify);
  window.addEventListener("storage", notify);
  return () => {
    window.removeEventListener(CHANGE_EVENT, notify);
    window.removeEventListener("storage", notify);
  };
}
