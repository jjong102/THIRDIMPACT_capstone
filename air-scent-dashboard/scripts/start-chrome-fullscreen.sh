#!/usr/bin/env bash
set -euo pipefail

URL="${KIOSK_URL:-http://127.0.0.1:5173}"
LOCK_FILE="${XDG_RUNTIME_DIR:-/tmp}/air-scent-chrome.lock"
USER_DATA="${HOME}/.local/share/air-scent-chromium"

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "Chromium fullscreen already starting/running" >&2
  exit 0
fi

if [[ -z "${DISPLAY:-}" ]]; then
  export DISPLAY="${DISPLAY:-:1}"
fi

for _ in $(seq 1 120); do
  if curl -sf -o /dev/null --max-time 1 "$URL"; then
    break
  fi
  sleep 1
done

if ! curl -sf -o /dev/null --max-time 2 "$URL"; then
  echo "Dashboard did not become ready at $URL" >&2
  exit 1
fi

BROWSER="${CHROMIUM_BIN:-}"
if [[ -z "$BROWSER" ]]; then
  if [[ -x /snap/bin/chromium ]]; then
    BROWSER="/snap/bin/chromium"
  elif command -v chromium >/dev/null 2>&1; then
    BROWSER="$(command -v chromium)"
  elif command -v chromium-browser >/dev/null 2>&1; then
    BROWSER="$(command -v chromium-browser)"
  else
    echo "Chromium을 찾을 수 없습니다." >&2
    exit 1
  fi
fi

mkdir -p "$USER_DATA"

KIOSK_ZOOM="${KIOSK_ZOOM:-1.75}"

exec "$BROWSER" \
  --user-data-dir="$USER_DATA" \
  --password-store=basic \
  --start-fullscreen \
  --kiosk \
  --force-device-scale-factor="$KIOSK_ZOOM" \
  --noerrdialogs \
  --disable-infobars \
  --disable-session-crashed-bubble \
  --hide-crash-restore-bubble \
  --disable-pinch \
  --overscroll-history-navigation=0 \
  --disable-features=OverscrollHistoryNavigation,TouchpadOverscrollHistoryNavigation,PullToRefresh,TouchDragDrop \
  --no-first-run \
  --no-default-browser-check \
  "$URL"
