#!/usr/bin/env python3
"""
mqtt_capture.py
배터리로 돌아가는 와드(firmware/ward)가 MQTT 로 보낸 측정값을 받아
capture_serial.py 와 똑같은 CSV 로 저장한다. (USB 대신 WiFi 로 녹화)

  [보드] ── MQTT "diffuser/ward-01/sensor" ──→ [이 스크립트] → training/recordings/rec_....csv
                                                  ├→ ward_controller.py (판정·청정기)
                                                  ├→ live_view.py       (그래프, 자동 실행)
                                                  └→ label_tool.py      (종료 후 라벨링, 자동 실행)

파일 형식이 capture_serial.py 와 같으므로 이후 도구(라벨링·학습·판정)를 그대로 쓴다.
보드는 Gas Valid 인 행만 보내므로 더미 행은 없다 (파서가 어차피 버리는 행이라 결과는 같다).

자격증명은 환경변수로 (코드·명령줄에 비밀번호를 쓰지 말 것):
  MQTT_BROKER  예: xxxx.s1.eu.hivemq.cloud
  MQTT_USER / MQTT_PASS
  MQTT_PORT    기본 8883 (TLS)

사용:
  python mqtt_capture.py --name demo_day1                       # 녹화 → Ctrl+C → 라벨링
  python mqtt_capture.py --name demo --no-label                 # 데모 당일 (라벨링 없이)
  python mqtt_capture.py --name demo --no-label --no-live       # 화면 없는 Jetson
  다른 터미널: python ward_controller.py --model openset_model.json --send

의존성: pip install paho-mqtt
"""

import argparse
import os
import ssl
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import RECORDINGS, DATA, LABELS                          # noqa: E402
from capture_serial import (HEADER, N_FIELDS,                      # noqa: E402
                            start_live_view, finish_recording)


class Recorder:
    """MQTT 메시지(여러 줄) → CSV. 네트워크와 분리해 두어 녹화 파일만으로도 시험할 수 있다."""

    def __init__(self, f):
        self.f = f
        self.rows = self.bad = self.msgs = 0
        self.dropped = 0
        self.last_t = None
        self.resets = 0
        self.last_rx = time.time()

    def feed_payload(self, text):
        self.msgs += 1
        self.last_rx = time.time()
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            if line.startswith("#"):
                self._meta(line)
                continue
            if line.startswith("TimeStamp"):
                continue
            parts = line.split(",")
            if len(parts) != N_FIELDS or not parts[0].isdigit():
                self.bad += 1
                continue
            t = int(parts[0])
            if self.last_t is not None and t < self.last_t - 1000:
                self.resets += 1
                print("\n[경고] 보드가 리셋됐습니다 (TimeStamp 가 0부터 다시 시작). "
                      "배터리 전압이 떨어졌을 수 있습니다. 라벨링 때 시간축이 튀면 여기서 나눠 쓰세요.")
            self.last_t = t
            self.f.write(line + "\n")
            self.rows += 1

    def _meta(self, line):
        if line.startswith("# dropped="):
            try:
                n = int(line.split("=", 1)[1])
            except ValueError:
                return
            if n > self.dropped:
                print(f"\n  (보드에서 연결이 끊긴 동안 버린 줄 누적 {n}개 — 그 구간은 비어 있습니다)")
                self.dropped = n


def make_client(client_id):
    import paho.mqtt.client as mqtt
    try:                                    # paho-mqtt 2.x
        return mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id=client_id)
    except AttributeError:                  # paho-mqtt 1.x
        return mqtt.Client(client_id=client_id)


def main():
    ap = argparse.ArgumentParser(description="MQTT 로 받은 BME688 측정값을 CSV 로 녹화")
    ap.add_argument("--topic", default="diffuser/ward-01/sensor",
                    help="와드 펌웨어 config.h 의 TOPIC_SENSOR 와 같아야 함 (10/13 이전 펌웨어는 ward/raw)")
    ap.add_argument("--out", type=Path, default=RECORDINGS, help="기본 training/recordings")
    ap.add_argument("--name", default=None, help="파일명에 붙일 태그(예: demo_day1)")
    ap.add_argument("--labels", nargs="+",
                    default=LABELS,
                    help="녹화 후 라벨링 UI 의 라벨 목록 (숫자키 1..N 순서)")
    ap.add_argument("--outdir", type=Path, default=DATA,
                    help="라벨 세그먼트 저장 루트 (기본 training/data)")
    ap.add_argument("--no-label", action="store_true", help="녹화 후 라벨링 UI 를 띄우지 않음")
    ap.add_argument("--no-live", action="store_true", help="실시간 그래프를 띄우지 않음")
    ap.add_argument("--insecure", action="store_true",
                    help="브로커 인증서 검증 생략 (기본은 검증. 인증서 오류가 날 때만)")
    args = ap.parse_args()

    from push_model import _env                # 환경변수, 없으면 Windows 사용자 환경변수(레지스트리)
    broker = _env("MQTT_BROKER").strip()
    user = _env("MQTT_USER").strip()
    pw = _env("MQTT_PASS")
    port = int(_env("MQTT_PORT") or "8883")
    if (not broker or not user or not pw) and sys.stdin.isatty():
        # 환경변수가 없으면 직접 묻는다 (비밀번호는 화면·명령 기록에 안 남게 getpass)
        import getpass
        print("MQTT 자격증명 환경변수가 없어 직접 입력받습니다. (secrets.h 와 같은 값)")
        broker = broker or input("  브로커 주소 (xxxx.s1.eu.hivemq.cloud): ").strip()
        user = user or input("  사용자 이름: ").strip()
        pw = pw or getpass.getpass("  비밀번호 (입력해도 안 보임): ")
    if not broker or not user:
        sys.exit("환경변수 MQTT_BROKER / MQTT_USER / MQTT_PASS 를 설정하세요.\n"
                 "  Linux:      export MQTT_BROKER=xxxx.s1.eu.hivemq.cloud MQTT_USER=... MQTT_PASS=...\n"
                 "  PowerShell: $env:MQTT_BROKER='xxxx.s1.eu.hivemq.cloud'; $env:MQTT_USER='...'; $env:MQTT_PASS='...'")
    try:
        client = make_client(f"WARD_CAPTURE_{os.getpid()}")
    except ImportError:
        sys.exit("paho-mqtt 필요: pip install paho-mqtt")

    args.out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    tag = f"_{args.name}" if args.name else ""
    out_path = args.out / f"rec_{stamp}{tag}.csv"
    print(f"[브로커] {broker}:{port}  토픽 {args.topic}")
    print(f"[저장] {out_path}")
    print("녹화 중... (Ctrl+C 로 종료)\n")

    f = open(out_path, "w", encoding="utf-8", newline="", buffering=1)   # 줄 단위로 디스크에
    f.write(HEADER + "\n")
    rec = Recorder(f)
    live = None if args.no_live else start_live_view(out_path)

    def on_connect(c, _u, _flags, rc):
        if rc == 0:
            c.subscribe(args.topic, qos=0)
            print(f"  [연결됨] {args.topic} 구독")
        else:
            print(f"  [연결 실패] 코드 {rc} (4·5 = 아이디/비밀번호 오류)")

    def on_disconnect(_c, _u, rc):
        if rc != 0:
            print("  [끊김] 자동 재접속 시도 중...")

    def on_message(_c, _u, msg):
        rec.feed_payload(msg.payload.decode("utf-8", errors="replace"))

    client.on_connect, client.on_disconnect, client.on_message = on_connect, on_disconnect, on_message
    client.username_pw_set(user, pw)
    if args.insecure:
        client.tls_set(cert_reqs=ssl.CERT_NONE)
        client.tls_insecure_set(True)
    else:
        client.tls_set()
    client.reconnect_delay_set(min_delay=1, max_delay=10)

    t0 = time.time()
    warned = False
    try:
        client.connect_async(broker, port, keepalive=60)
        client.loop_start()
        while True:
            time.sleep(1.0)
            idle = time.time() - rec.last_rx
            if idle > 20 and not warned:
                print(f"\n  ⚠ {idle:.0f}초째 데이터가 없습니다. 보드 전원·WiFi·LED(1초 점멸이면 연결됨)를 확인하세요.")
                warned = True
            elif idle < 5:
                warned = False
            print(f"\r  행 {rec.rows}개  메시지 {rec.msgs}개  ({time.time() - t0:5.0f}s)",
                  end="", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        client.loop_stop()
        try:
            client.disconnect()
        except Exception:
            pass
        f.close()
        print(f"\n\n[완료] {rec.rows}행 저장 → {out_path}")
        if rec.bad:
            print(f"  (깨진 줄 {rec.bad}개 제외)")
        if rec.resets:
            print(f"  (보드 리셋 {rec.resets}회)")
        if rec.rows == 0:
            print("주의: 데이터가 없습니다. 보드 펌웨어(firmware/ward)·토픽·자격증명을 확인하세요.")

    finish_recording(out_path, rec.rows, live, args)


if __name__ == "__main__":
    main()
