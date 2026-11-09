#!/usr/bin/env python3
"""
ward_view.py
와드가 MQTT 로 보내는 **온디바이스 판정**(diffuser/<id>/inference_ward)을 한 줄씩 보여준다. 배터리 와드 확인용.

  12:03:41  가동  812s  악취      Vinegar   세기 0.51  표 7/8  청정기 ON  ◀ 출동
  - 가동 시간이 줄어들면 "재부팅" 으로 표시 (Brownout 등)
  - 원시값 메시지(/sensor) 개수도 30초마다 보여 줘 연결이 살아 있는지 알 수 있다
  - 모델 교체 답장(/model/ack)도 같이 보여 준다

자격증명은 mqtt_capture.py 와 같은 환경변수 (MQTT_BROKER / MQTT_USER / MQTT_PASS).

사용:
  python ward_view.py                # ward-01, Ctrl+C 로 종료
  python ward_view.py --device ward-02
  python ward_view.py --rebase       # 기준선만 다시 잡기 (센서는 데워진 채) → 답장 받고 끝
  python ward_view.py --reboot       # 와드 재시작 (RESET 버튼과 같음, 켠 지 30초 안에는 무시)
  python ward_view.py --spray 90     # "지금부터 90초 향을 뿌린다" → 뿌리는 동안 + 180초, 모르는 냄새로는 출동 안 함
                                     # (실제로는 발향하는 쪽(fragrance_bridge)이 보낸다 — INTERFACE.md)
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mqtt_capture import make_client                      # noqa: E402
from push_model import credentials                        # noqa: E402

STATE_KO = {"WARMUP": "예열 중", "CLEAN": "깨끗함", "SCENT": "향기", "MALODOR": "악취",
            "UNKNOWN": "모르는냄새", "HOLD": "보류"}


def main():
    ap = argparse.ArgumentParser(description="와드 온디바이스 판정 MQTT 보기")
    ap.add_argument("--device", default="ward-01")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--rebase", action="store_true", help="와드에 기준선 다시 잡기 명령을 보내고 끝")
    g.add_argument("--reboot", action="store_true", help="와드에 재시작 명령을 보내고 끝")
    g.add_argument("--spray", type=int, metavar="초", help="와드에 'N초 향을 뿌린다' 알림을 보내고 끝")
    args = ap.parse_args()
    if args.rebase or args.reboot:
        return send_cmd(args.device, "REBASE" if args.rebase else "REBOOT")
    if args.spray is not None:
        return send_cmd(args.device, f"SPRAY {max(0, args.spray)}")
    broker, port, user, pw = credentials()
    base = f"diffuser/{args.device}"
    st = {"sensor": 0, "up": None, "last": time.time()}
    c = make_client(f"WARD_VIEW_{os.getpid()}")

    def on_connect(cl, _u, _f, rc):
        if rc == 0:
            cl.subscribe(f"{base}/#", qos=0)
            print(f"[연결됨] {base}/#  — 와드 판정은 약 10초마다 (예열 중이면 WARMUP)\n")
        else:
            print(f"[연결 실패] 코드 {rc}")

    def on_message(_c, _u, msg):
        t = msg.topic
        if t.endswith("/sensor"):
            st["sensor"] += 1
            return
        try:
            d = json.loads(msg.payload.decode("utf-8", "replace"))
        except ValueError:
            return
        if t.endswith("/model/ack"):
            print(f"  [와드 답장] {d.get('result')}  model={d.get('model')}  {d.get('msg', '')}")
            return
        if not t.endswith("/inference_ward"):
            return
        up = d.get("uptime_s")
        if st["up"] is not None and up is not None and up < st["up"]:
            print(f"  ⚠ 와드 재부팅됨 (가동 {st['up']}s → {up}s)")
        st["up"] = up
        state = d.get("state", "")
        line = f"{time.strftime('%H:%M:%S')}  가동 {up:>5}s  {STATE_KO.get(state, state):6s}"
        if state != "WARMUP":
            note = f"({d.get('label_note')})" if d.get("label_note") else ""
            line += (f"  {d.get('label', ''):9s}{note}  세기 {d.get('mag', 0):.2f}  표 {d.get('votes')}/{d.get('n')}  "
                     f"청정기 {'ON' if d.get('purifier') == 'on' else 'off'}")
            if d.get("event") == "ON":
                line += "  ◀ 출동"
            elif d.get("event") == "OFF":
                line += "  ◀ 끔"
        print(line, flush=True)

    c.on_connect, c.on_message = on_connect, on_message
    c.username_pw_set(user, pw)
    c.tls_set()
    c.connect(broker, port, 30)
    c.loop_start()
    try:
        while True:
            time.sleep(30)
            print(f"  … 원시값 메시지 30초에 {st['sensor']}개", flush=True)
            st["sensor"] = 0
    except KeyboardInterrupt:
        pass
    finally:
        c.loop_stop()


def send_cmd(device, cmd, wait=15.0):
    """diffuser/<id>/cmd 로 명령 1개 (retained 아님) → /model/ack 답장을 기다린다."""
    broker, port, user, pw = credentials()
    base = f"diffuser/{device}"
    got = {}
    c = make_client(f"WARD_CMD_{os.getpid()}")

    def on_connect(cl, _u, _f, rc):
        if rc != 0:
            print(f"[연결 실패] 코드 {rc}")
            return
        cl.subscribe(f"{base}/model/ack", qos=0)
        cl.publish(f"{base}/cmd", cmd, qos=0, retain=False)
        print(f"[보냄] {base}/cmd ← {cmd}")

    def on_message(_c, _u, msg):
        try:
            d = json.loads(msg.payload.decode("utf-8", "replace"))
        except ValueError:
            return
        if d.get("result") in ("rebase", "reboot", "spray"):
            got.update(d)

    c.on_connect, c.on_message = on_connect, on_message
    c.username_pw_set(user, pw)
    c.tls_set()
    c.connect(broker, port, 30)
    c.loop_start()
    t0 = time.time()
    while not got and time.time() - t0 < wait:
        time.sleep(0.2)
    c.loop_stop()
    if got:
        print(f"[와드 답장] {got.get('result')}  {got.get('msg', '')}  (가동 {got.get('uptime_s')}s)")
        return 0
    print(f"[답장 없음] {wait:.0f}초 — 와드가 꺼져 있거나, 이 명령이 없는 옛 펌웨어일 수 있음")
    return 1


if __name__ == "__main__":
    sys.exit(main())
