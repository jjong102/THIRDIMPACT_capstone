#!/usr/bin/env python3
"""로봇 젯슨에서 실행. 유선으로 hostname / whoami / hostname -I 를 알려 줍니다."""

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
