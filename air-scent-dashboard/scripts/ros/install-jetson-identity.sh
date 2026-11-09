#!/usr/bin/env bash
# 로봇 젯슨에서 한 번만 실행. 이후 부팅마다 identity가 켜집니다.
# 파일이 없으면 대시보드에서 받으세요:
#   curl -fsSL http://192.168.10.1:5173/jetson-identity-install.sh | bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  exec sudo -E "$0" "$@"
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_USER="${SUDO_USER:-${ROBOT_JETSON_USER:-moonshot}}"

if [[ -f "$ROOT/jetson_identity.py" ]]; then
  install -m 0755 "$ROOT/jetson_identity.py" /usr/local/sbin/jetson_identity.py
else
  echo "[identity] jetson_identity.py 가 없어 내장 복사본을 씁니다"
  curl -fsSL http://192.168.10.1:5173/jetson-identity-install.sh | bash
  exit 0
fi

cat > /etc/systemd/system/jetson-identity.service <<EOF
[Unit]
Description=Jetson identity (hostname / whoami / 10.96 IP)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${RUN_USER}
ExecStart=/usr/bin/python3 /usr/local/sbin/jetson_identity.py
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now jetson-identity.service

echo
echo "[identity] 설치 완료 — 부팅마다 ${RUN_USER} 로 자동 시작"
systemctl is-active jetson-identity.service
curl -sS http://127.0.0.1:5181/ || true
echo
