#!/usr/bin/env bash
# 로봇 젯슨에서 실행. 이 젯슨(192.168.10.1)에서 받아도 됩니다.
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  exec sudo -E "$0" "$@"
fi

RUN_USER="${SUDO_USER:-${ROBOT_JETSON_USER:-moonshot}}"

cat > /usr/local/sbin/jetson_identity.py <<'PY'
#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 5181


def run(cmd: list[str]) -> str:
    return subprocess.check_output(cmd, text=True).strip()


def identity() -> dict:
    addresses = [ip for ip in run(["hostname", "-I"]).split() if ip]
    return {
        "ok": True,
        "hostname": run(["hostname"]),
        "user": run(["whoami"]),
        "addresses": addresses,
    }


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = json.dumps(identity(), ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args) -> None:
        return


def main() -> None:
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"[identity] 0.0.0.0:{PORT}  hostname={run(['hostname'])}  whoami={run(['whoami'])}")
    print(f"[identity] IPs={run(['hostname', '-I'])}")
    server.serve_forever()


if __name__ == "__main__":
    main()
PY
chmod 0755 /usr/local/sbin/jetson_identity.py

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
systemctl --no-pager --full status jetson-identity.service | head -12
curl -sS http://127.0.0.1:5181/ || true
echo
