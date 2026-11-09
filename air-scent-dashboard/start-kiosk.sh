#!/bin/bash

cd ~/THIRDIMPACT_capstone/air-scent-dashboard

pkill -f "vite" 2>/dev/null

npm run dev -- --host 0.0.0.0 &
sleep 4

chromium-browser \
  --kiosk http://localhost:5173 \
  --force-device-scale-factor=1.75 \
  --disable-pinch \
  --overscroll-history-navigation=0 \
  --disable-features=TouchpadOverscrollHistoryNavigation \
  --noerrdialogs \
  --disable-infobars \
  --disable-session-crashed-bubble
