"""
COM5 시리얼 출력을 읽어 HiveMQ Cloud(MQTT)로 발행하는 브리지.
- BME688 selectivity 추론 결과(bme688_sel_esp32.ino)를 한 줄씩 그대로 publish.
- 수신은 기존 Python 수신 스크립트(같은 토픽 구독)로 확인.

주의: COM5는 한 번에 한 프로그램만 열 수 있습니다.
      이 스크립트를 돌리는 동안에는 Arduino 시리얼 모니터를 닫아 두세요.
"""

import os
import ssl
import time
from datetime import datetime

import serial                     # pyserial
import paho.mqtt.client as mqtt

# ---- 설정 ----------------------------------------------------------------
# 자격증명은 환경변수로 주입하세요 (커밋 금지). 예:
#   set MQTT_BROKER=your-cluster.s1.eu.hivemq.cloud   (Windows)
#   set MQTT_USER=...  &  set MQTT_PASS=...
SERIAL_PORT = os.environ.get("SERIAL_PORT", "COM5")
BAUD_RATE   = int(os.environ.get("BAUD_RATE", "115200"))

MQTT_BROKER = os.environ.get("MQTT_BROKER", "your-cluster-id.s1.eu.hivemq.cloud")
MQTT_PORT   = int(os.environ.get("MQTT_PORT", "8883"))
MQTT_USER   = os.environ.get("MQTT_USER", "YOUR_MQTT_USER")
MQTT_PASS   = os.environ.get("MQTT_PASS", "YOUR_MQTT_PASSWORD")
MQTT_TOPIC  = os.environ.get("MQTT_TOPIC", "sensor/air_quality")
# -------------------------------------------------------------------------


def on_connect(client, userdata, flags, rc):
    codes = {
        0: "연결 성공",
        1: "프로토콜 버전 오류",
        2: "클라이언트 ID 거부",
        3: "브로커 사용 불가",
        4: "인증 실패",
        5: "권한 없음",
    }
    print(f"[MQTT] {codes.get(rc, f'알 수 없는 오류 (rc={rc})')}")


def on_disconnect(client, userdata, rc):
    if rc != 0:
        print(f"[MQTT] 예기치 않은 연결 끊김 (rc={rc}), 자동 재연결 시도 중...")


def make_client():
    client = mqtt.Client(client_id="ESP32_Serial_Publisher",
                         protocol=mqtt.MQTTv311)
    client.username_pw_set(MQTT_USER, MQTT_PASS)
    client.tls_set(cert_reqs=ssl.CERT_NONE)
    client.tls_insecure_set(True)
    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    return client


def open_serial():
    """포트가 준비될 때까지 재시도하며 시리얼을 연다."""
    while True:
        try:
            ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1)
            print(f"[시리얼] {SERIAL_PORT} @ {BAUD_RATE} 연결됨")
            return ser
        except serial.SerialException as e:
            print(f"[시리얼] {SERIAL_PORT} 열기 실패 ({e}). "
                  f"시리얼 모니터가 열려 있지 않은지 확인하세요. 3초 후 재시도...")
            time.sleep(3)


def main():
    print(f"[시작] HiveMQ Cloud 연결 중... {MQTT_BROKER}:{MQTT_PORT}")
    client = make_client()
    client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
    client.loop_start()          # 백그라운드에서 네트워크/재연결 처리

    ser = open_serial()

    try:
        while True:
            try:
                raw = ser.readline()
            except serial.SerialException:
                print("[시리얼] 연결 끊김. 재연결 시도...")
                try:
                    ser.close()
                except Exception:
                    pass
                ser = open_serial()
                continue

            if not raw:
                continue         # timeout, 빈 읽기

            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue

            client.publish(MQTT_TOPIC, line, qos=0)
            ts = datetime.now().strftime("%H:%M:%S")
            print(f"[{ts}] -> {line}")

    except KeyboardInterrupt:
        print("\n[종료] 프로그램을 종료합니다.")
    finally:
        try:
            ser.close()
        except Exception:
            pass
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
