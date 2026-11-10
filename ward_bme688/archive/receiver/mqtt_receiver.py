"""
Subscribe to the BME688 scent classifier's MQTT topic and print each result.

The ESP32 publishes a single majority-vote label (air / woody / citrus /
floral / unknown) to the topic below, roughly every 2 seconds.

Setup (once):
    pip3 install "paho-mqtt==1.6.1"

Run:
    python3 mqtt_receiver.py

Configure via environment variables, or edit the defaults below:
    MQTT_BROKER, MQTT_USER, MQTT_PASS, MQTT_TOPIC
"""

import os
import ssl
from datetime import datetime

import paho.mqtt.client as mqtt

MQTT_BROKER = os.environ.get("MQTT_BROKER", "your-cluster-id.s1.eu.hivemq.cloud")
MQTT_PORT   = int(os.environ.get("MQTT_PORT", "8883"))
MQTT_USER   = os.environ.get("MQTT_USER", "YOUR_MQTT_USERNAME")
MQTT_PASS   = os.environ.get("MQTT_PASS", "YOUR_MQTT_PASSWORD")
MQTT_TOPIC  = os.environ.get("MQTT_TOPIC", "sensor/air_quality")


def on_connect(client, userdata, flags, rc):
    codes = {
        0: "connected",
        1: "wrong protocol version",
        2: "client id rejected",
        3: "broker unavailable",
        4: "bad username/password",
        5: "not authorized",
    }
    print(f"[MQTT] {codes.get(rc, f'error rc={rc}')}")
    if rc == 0:
        client.subscribe(MQTT_TOPIC)
        print(f"[SUB]  {MQTT_TOPIC}\n")


def on_message(client, userdata, msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg.payload.decode('utf-8', 'replace')}")


def on_disconnect(client, userdata, rc):
    if rc != 0:
        print(f"[MQTT] unexpected disconnect (rc={rc}), reconnecting...")


def main():
    client = mqtt.Client(client_id="BME688_Receiver", protocol=mqtt.MQTTv311)
    client.username_pw_set(MQTT_USER, MQTT_PASS)
    client.tls_set(cert_reqs=ssl.CERT_NONE)
    client.tls_insecure_set(True)
    client.on_connect = on_connect
    client.on_message = on_message
    client.on_disconnect = on_disconnect

    print(f"[START] connecting to {MQTT_BROKER}:{MQTT_PORT}")
    client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
    try:
        client.loop_forever()
    except KeyboardInterrupt:
        print("\n[EXIT]")
        client.disconnect()


if __name__ == "__main__":
    main()
