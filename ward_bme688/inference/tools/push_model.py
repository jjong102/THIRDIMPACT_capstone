#!/usr/bin/env python3
"""
push_model.py
판정 모델을 **MQTT 로 와드에 바로** 보낸다. 펌웨어를 다시 올릴 필요가 없다.

  inference/openset_model.json ──(글자 형식 + CRC)──▶ diffuser/ward-01/model  (retained)
                                                         │ 와드: 검사 → 적용 → 내부 저장소(NVS) 저장
  ◀── diffuser/ward-01/model/ack  {"result":"ok", "model":"<id>", ...} ──┘

- retained 로 보내므로 와드가 지금 꺼져 있어도, 켜져서 MQTT 에 붙는 순간 받는다.
- 와드는 받은 모델을 검사한다(체크섬, 숫자 범위, 방향 벡터 길이). 실패하면 지금 모델을 그대로 쓰고 error 로 답한다.
- 같은 모델(id 같음)이면 "same" 으로 답하고 아무것도 바꾸지 않는다.
- --reset: 저장된 모델을 지우고 펌웨어 기본 모델(model.h)로 되돌린다.

자격증명은 mqtt_capture.py 와 같은 환경변수 (MQTT_BROKER / MQTT_USER / MQTT_PASS, MQTT_PORT 기본 8883).

사용:
  python push_model.py                    # 기본 모델 json 을 보내고 와드 답장을 최대 60초 기다림
  python push_model.py --dry-run          # 보낼 글자만 출력
  python push_model.py --reset            # 와드를 기본 모델로
  python push_model.py --wait 0           # 답장 안 기다림 (와드가 꺼져 있을 때 — 켜지면 받음)
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
from paths import MODEL                                   # noqa: E402
from export_model import model_text, model_id             # noqa: E402

DEVICE_ID = "ward-01"          # inference/firmware/ward_mqtt/config.h 와 같아야 함


def _env(name):
    """환경변수. 없으면 Windows 사용자 환경변수(레지스트리)에서 — 설정 후 VS Code 를 안 껐다 켠 경우 대비."""
    v = os.environ.get(name, "")
    if not v and sys.platform == "win32":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
                v = winreg.QueryValueEx(k, name)[0]
        except OSError:
            v = ""
    return v


def credentials():
    broker = _env("MQTT_BROKER").strip()
    user = _env("MQTT_USER").strip()
    pw = _env("MQTT_PASS")
    if (not broker or not user or not pw) and sys.stdin.isatty():
        import getpass
        print("MQTT 자격증명 환경변수가 없어 직접 입력받습니다.")
        broker = broker or input("  브로커 주소: ").strip()
        user = user or input("  사용자 이름: ").strip()
        pw = pw or getpass.getpass("  비밀번호 (입력해도 안 보임): ")
    if not broker or not user:
        sys.exit("환경변수 MQTT_BROKER / MQTT_USER / MQTT_PASS 를 설정하세요.")
    return broker, int(_env("MQTT_PORT") or "8883"), user, pw


def push(payload, device=DEVICE_ID, wait=60.0, insecure=False, expect_id=None, clear_first=False, retain=True):
    """반환: 와드 답장 dict 또는 None(시간 초과·답장 안 기다림)"""
    from mqtt_capture import make_client
    topic, ack_topic = f"diffuser/{device}/model", f"diffuser/{device}/model/ack"
    broker, port, user, pw = credentials()
    acks = []
    done = {"pub": False}
    c = make_client(f"WARD_PUSH_{os.getpid()}")

    def on_connect(cl, _u, _f, rc):
        if rc != 0:
            print(f"[연결 실패] 코드 {rc} (4·5 = 아이디/비밀번호)")
            return
        cl.subscribe(ack_topic, qos=1)
        if clear_first:                       # retained 지우기 (빈 메시지)
            cl.publish(topic, b"", qos=1, retain=True)
        info = cl.publish(topic, payload.encode("ascii"), qos=1, retain=retain)
        info.wait_for_publish(10)
        done["pub"] = True

    def on_message(_c, _u, msg):
        try:
            acks.append(json.loads(msg.payload.decode("utf-8", errors="replace")))
        except ValueError:
            pass

    c.on_connect, c.on_message = on_connect, on_message
    c.username_pw_set(user, pw)
    if insecure:
        c.tls_set(cert_reqs=ssl.CERT_NONE)
        c.tls_insecure_set(True)
    else:
        c.tls_set()
    c.connect(broker, port, keepalive=30)
    c.loop_start()
    t0 = time.time()
    try:
        while time.time() - t0 < 15 and not done["pub"]:
            time.sleep(0.2)
        if not done["pub"]:
            print("✗ 발행 실패 (브로커 연결 확인)")
            return None
        print(f"[보냄] {topic}  ({len(payload)}바이트, retained={'예' if retain else '아니오'})")
        if wait <= 0:
            return None
        print(f"[대기] 와드 답장 {ack_topic} (최대 {wait:.0f}초) …")
        while time.time() - t0 < wait:
            for a in acks:
                if expect_id is None or a.get("model") == expect_id or a.get("result") in ("error", "reset"):
                    return a
            time.sleep(0.5)
        return None
    finally:
        c.loop_stop()
        c.disconnect()


def main():
    ap = argparse.ArgumentParser(description="판정 모델을 MQTT 로 와드에 보냄")
    ap.add_argument("--model", type=Path, default=MODEL)
    ap.add_argument("--device", default=DEVICE_ID)
    ap.add_argument("--on", nargs="*", default=None, help="출동 라벨 (기본: 모델의 on_labels)")
    ap.add_argument("--wait", type=float, default=60.0, help="와드 답장 기다릴 초 (0 = 안 기다림)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--reset", action="store_true", help="와드를 펌웨어 기본 모델로 되돌림")
    ap.add_argument("--insecure", action="store_true")
    args = ap.parse_args()

    if args.reset:
        ack = push("WARDMODEL RESET\n", args.device, args.wait, args.insecure,
                   clear_first=True, retain=False)
        print(f"[답장] {ack}" if ack else "답장 없음 (와드가 꺼져 있거나 연결 안 됨)")
        return

    model = json.loads(args.model.read_text(encoding="utf-8"))
    on = args.on if args.on is not None else model.get("on_labels", ["Coffee", "Vinegar"])
    txt = model_text(model, set(on))
    mid = model_id(model)
    print(f"[모델] {args.model.name}  id {mid}  클래스 {', '.join(sorted(model['classes']))}  출동 {', '.join(on)}")
    if args.dry_run:
        print(txt, end="")
        return
    ack = push(txt, args.device, args.wait, args.insecure, expect_id=mid)
    if ack is None:
        if args.wait > 0:
            print("답장 없음 — 와드가 꺼져 있거나 MQTT 에 안 붙어 있음. retained 라 켜지면 받는다.")
        return
    r = ack.get("result")
    if r in ("ok", "same"):
        print(f"✓ 와드 적용됨: model {ack.get('model')} ({'이미 같은 모델' if r == 'same' else '새 모델, 저장됨'})")
    elif r == "ok_nosave":
        print(f"△ 적용됐지만 저장 실패 — 재부팅하면 이전 모델로 돌아감: {ack}")
    else:
        print(f"✗ 와드가 거절: {ack.get('msg')}  (와드는 지금 모델 {ack.get('model')} 을 계속 씀)")
        sys.exit(1)


if __name__ == "__main__":
    main()
