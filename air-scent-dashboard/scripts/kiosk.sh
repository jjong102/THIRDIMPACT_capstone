#!/usr/bin/env bash
set -euo pipefail

KIOSK_URL="${KIOSK_URL:-http://localhost:5173}"
CHROMIUM_USER_DATA="${CHROMIUM_USER_DATA:-/tmp/air-scent-chromium}"

if [[ -n "${CHROMIUM_BIN:-}" ]]; then
  BROWSER="$CHROMIUM_BIN"
elif command -v chromium-browser >/dev/null 2>&1; then
  BROWSER="chromium-browser"
elif command -v chromium >/dev/null 2>&1; then
  BROWSER="chromium"
elif command -v google-chrome >/dev/null 2>&1; then
  BROWSER="google-chrome"
elif command -v google-chrome-stable >/dev/null 2>&1; then
  BROWSER="google-chrome-stable"
else
  echo "Chromium/Chrome을 찾을 수 없습니다. CHROMIUM_BIN 환경변수로 경로를 지정하세요." >&2
  exit 1
fi

exec "$BROWSER" \
  --user-data-dir="$CHROMIUM_USER_DATA" \
  --kiosk \
  --force-device-scale-factor="${KIOSK_ZOOM:-1.75}" \
  --noerrdialogs \
  --disable-infobars \
  --disable-session-crashed-bubble \
  --overscroll-history-navigation=0 \
  --disable-pinch \
  --disable-features=OverscrollHistoryNavigation,TouchpadOverscrollHistoryNavigation \
  "$KIOSK_URL"
