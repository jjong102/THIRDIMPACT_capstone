#!/usr/bin/env python3
"""
iaq_view.py
공기질 센서(bme688_iaq_mqtt)가 보내는 IAQ 를 받아 한 줄씩 보여준다. 동작 확인용.

자격증명은 mqtt_capture.py 와 같은 환경변수: MQTT_BROKER / MQTT_USER / MQTT_PASS (MQTT_PORT 기본 8883)

사용:
  python iaq_view.py                    # diffuser/ward-01/iaq
  python iaq_view.py --topic diffuser/+/iaq
"""

import argparse
import json
import os
import ssl
import sys
import time
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mqtt_capture import make_client      # noqa: E402

LEVEL_KO = {
    "excellent": "아주 좋음", "good": "좋음", "lightly_polluted": "약간 나쁨",
    "moderately_polluted": "나쁨", "heavily_polluted": "많이 나쁨",
    "severely_polluted": "매우 나쁨", "extremely_polluted": "최악", "unknown": "-",
}
ACC_KO = {0: "시작 직후(무의미)", 1: "보정 중", 2: "거의 됨", 3: "보정 완료"}


def fmt(v, spec):
    return "-" if v is None else format(v, spec)


def main():
    ap = argparse.ArgumentParser(description="공기질(IAQ) MQTT 수신 확인")
    ap.add_argument("--topic", default="diffuser/ward-01/iaq")
    ap.add_argument("--insecure", action="store_true", help="브로커 인증서 검증 생략")
    args = ap.parse_args()

    broker = os.environ.get("MQTT_BROKER", "").strip()
    user = os.environ.get("MQTT_USER", "").strip()
    pw = os.environ.get("MQTT_PASS", "")
    port = int(os.environ.get("MQTT_PORT", "8883"))
    if not broker or not user:
        sys.exit("환경변수 MQTT_BROKER / MQTT_USER / MQTT_PASS 를 설정하세요 (mqtt_capture.py 와 같음).")
    client = make_client(f"IAQ_VIEW_{os.getpid()}")

    def on_connect(c, _u, _f, rc):
        print(f"[연결됨] {args.topic}" if rc == 0 else f"[연결 실패] 코드 {rc}")
        if rc == 0:
            c.subscribe(args.topic)

    def on_message(_c, _u, msg):
        try:
            d = json.loads(msg.payload.decode("utf-8", errors="replace"))
        except ValueError:
            print(f"(JSON 아님) {msg.payload[:80]!r}")
            return
        acc = d.get("iaq_acc", 0)
        print(f"{time.strftime('%H:%M:%S')}  IAQ {fmt(d.get('iaq'), '5.0f')} "
              f"{LEVEL_KO.get(d.get('level'), d.get('level')):6s}  정확도 {acc}({ACC_KO.get(acc, '?')})  "
              f"CO2eq {fmt(d.get('co2_eq'), '5.0f')}ppm  bVOC {fmt(d.get('bvoc_eq'), '5.2f')}ppm  "
              f"{fmt(d.get('temp'), '4.1f')}℃ {fmt(d.get('hum'), '3.0f')}%  RSSI {d.get('rssi')}")

    client.on_connect, client.on_message = on_connect, on_message
    client.username_pw_set(user, pw)
    if args.insecure:
        client.tls_set(cert_reqs=ssl.CERT_NONE)
        client.tls_insecure_set(True)
    else:
        client.tls_set()
    client.connect(broker, port, keepalive=60)
    try:
        client.loop_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
