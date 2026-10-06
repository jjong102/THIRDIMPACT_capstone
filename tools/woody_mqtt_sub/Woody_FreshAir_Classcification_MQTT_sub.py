import ssl
import time
from datetime import datetime
import paho.mqtt.client as mqtt

MQTT_BROKER    = "d8068936862341f4b071c74b052efdf7.s1.eu.hivemq.cloud"
MQTT_PORT      = 8883
MQTT_USER      = "third_impact"
MQTT_PASS      = "Ti000000"
MQTT_TOPIC     = "sensor/air_quality"

def on_connect(client, userdata, flags, rc):
    codes = {
        0: "연결 성공",
        1: "프로토콜 버전 오류",
        2: "클라이언트 ID 거부",
        3: "브로커 사용 불가",
        4: "인증 실패",
        5: "권한 없음",
    }
    print(f"[연결] {codes.get(rc, f'알 수 없는 오류 (rc={rc})')}")
    if rc == 0:
        client.subscribe(MQTT_TOPIC)
        print(f"[구독] 토픽: {MQTT_TOPIC}\n")

def on_message(client, userdata, msg):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    payload   = msg.payload.decode("utf-8")
    print(f"[{timestamp}] {payload}")

def on_disconnect(client, userdata, rc):
    if rc != 0:
        print(f"[경고] 예기치 않은 연결 끊김 (rc={rc}), 재연결 시도 중...")

def main():
    client = mqtt.Client(client_id="Python_Receiver", protocol=mqtt.MQTTv311)
    client.username_pw_set(MQTT_USER, MQTT_PASS)
    client.tls_set(cert_reqs=ssl.CERT_NONE)
    client.tls_insecure_set(True)

    client.on_connect    = on_connect
    client.on_message    = on_message
    client.on_disconnect = on_disconnect

    print(f"[시작] HiveMQ Cloud 브로커 연결 중...")
    print(f"       {MQTT_BROKER}:{MQTT_PORT}")
    client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)

    try:
        client.loop_forever()
    except KeyboardInterrupt:
        print("\n[종료] 프로그램을 종료합니다.")
        client.disconnect()

if __name__ == "__main__":
    main()
