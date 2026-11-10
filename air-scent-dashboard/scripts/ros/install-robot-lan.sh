#!/usr/bin/env bash
# 한 번만 실행 (sudo). 이후 부팅마다 유선 192.168.10.1 이 붙습니다.
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  exec sudo -E "$0" "$@"
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IFACE="${ROBOT_LAN_IFACE:-enP8p1s0}"
ADDR="${ROBOT_LAN_ADDR:-192.168.10.1/24}"
CON_NAME="${ROBOT_LAN_CON:-Robot LAN}"
NM_DST="/etc/NetworkManager/system-connections/Robot LAN.nmconnection"

install -m 0755 "$ROOT/ensure-robot-lan.sh" /usr/local/sbin/ensure-robot-lan.sh
install -m 0644 "$ROOT/robot-lan.service" /etc/systemd/system/robot-lan.service

if [[ -d /etc/NetworkManager/system-connections ]]; then
  install -m 0600 "$ROOT/robot-lan.nmconnection" "$NM_DST"
fi

if command -v nmcli >/dev/null 2>&1; then
  if nmcli -t -f NAME connection show | grep -Fxq "Wired connection 1"; then
    nmcli connection modify "Wired connection 1" \
      connection.autoconnect no \
      connection.interface-name "$IFACE" || true
  fi
  nmcli connection reload || true
  nmcli connection up "$CON_NAME" || true
fi

systemctl daemon-reload
systemctl enable --now robot-lan.service

/usr/local/sbin/ensure-robot-lan.sh

echo
echo "[robot-lan] 설치 완료 — 부팅 때마다 ${IFACE} ${ADDR}"
ip -4 -br addr show "$IFACE" || true
