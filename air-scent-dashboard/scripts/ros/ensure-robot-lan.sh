#!/usr/bin/env bash
# 로봇 LAN: 이 젯슨 192.168.10.1 ↔ 상대 젯슨 192.168.10.2
set -euo pipefail

IFACE="${ROBOT_LAN_IFACE:-enP8p1s0}"
ADDR="${ROBOT_LAN_ADDR:-192.168.10.1/24}"
CON_NAME="${ROBOT_LAN_CON:-Robot LAN}"

if [[ ! -e "/sys/class/net/${IFACE}" ]]; then
  echo "[robot-lan] ${IFACE} 없음" >&2
  exit 1
fi

ip link set "$IFACE" up

if command -v nmcli >/dev/null 2>&1; then
  if nmcli -t -f NAME connection show | grep -Fxq "$CON_NAME"; then
    nmcli connection up "$CON_NAME" >/dev/null 2>&1 || true
  fi
fi

if ip -4 -o addr show dev "$IFACE" | grep -q ' inet 192.168.10.1/'; then
  echo "[robot-lan] ${IFACE} already ${ADDR}"
  exit 0
fi

ip addr add "$ADDR" dev "$IFACE"
echo "[robot-lan] added ${ADDR} on ${IFACE}"
