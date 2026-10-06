#!/usr/bin/env python3
"""Bridge scent/air-quality readings from the HiveMQ broker onto ROS2.

Each room "ward" publishes a reading such as ``woody(98%)`` over MQTT.
This node subscribes to those messages and republishes them as JSON on a
ROS2 topic so other nodes (e.g. web_server_node) don't need their own MQTT
connection / credentials.
"""

import json
import os
import re
import ssl
from datetime import datetime, timezone

import paho.mqtt.client as mqtt
import rclpy
from rclpy.node import Node
from std_msgs.msg import String

# name(percent%) e.g. "woody(98%)", "citrus (90.5 %)", "Fresh Air(99%)"
READING_PATTERN = re.compile(r'([A-Za-z_][A-Za-z_ ]*?)\s*\(\s*(\d+(?:\.\d+)?)\s*%\s*\)')


class MqttSubNode(Node):

    def __init__(self):
        super().__init__('mqtt_sub_node')

        self.declare_parameter('mqtt_broker', os.environ.get('MQTT_BROKER', ''))
        self.declare_parameter('mqtt_port', 8883)
        self.declare_parameter('mqtt_user', os.environ.get('MQTT_USER', ''))
        self.declare_parameter('mqtt_pass', os.environ.get('MQTT_PASS', ''))
        # trailing /# lets each room ward publish on its own sub-topic,
        # e.g. sensor/air_quality/room_1 -- the segment after the base
        # becomes room_id. A bare "sensor/air_quality" message (no room
        # segment) still matches and is reported under room_id "default".
        self.declare_parameter('mqtt_topic', 'sensor/air_quality/#')
        self.declare_parameter('ros_topic', 'scent/raw')

        self.mqtt_broker = self.get_parameter('mqtt_broker').value
        self.mqtt_port = self.get_parameter('mqtt_port').value
        self.mqtt_user = self.get_parameter('mqtt_user').value
        self.mqtt_pass = self.get_parameter('mqtt_pass').value
        self.mqtt_topic = self.get_parameter('mqtt_topic').value
        self.topic_base = self.mqtt_topic.split('/#')[0].rstrip('/')

        self.publisher_ = self.create_publisher(String, self.get_parameter('ros_topic').value, 10)

        self.client = mqtt.Client(client_id='GDM_mqtt_sub_node', protocol=mqtt.MQTTv311)
        self.client.username_pw_set(self.mqtt_user, self.mqtt_pass)
        self.client.tls_set(cert_reqs=ssl.CERT_NONE)
        self.client.tls_insecure_set(True)
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message
        self.client.on_disconnect = self.on_disconnect

        self.get_logger().info(f'Connecting to {self.mqtt_broker}:{self.mqtt_port} ...')
        self.client.connect(self.mqtt_broker, self.mqtt_port, keepalive=60)
        self.client.loop_start()

    def on_connect(self, client, userdata, flags, rc):
        codes = {
            0: '연결 성공',
            1: '프로토콜 버전 오류',
            2: '클라이언트 ID 거부',
            3: '브로커 사용 불가',
            4: '인증 실패',
            5: '권한 없음',
        }
        self.get_logger().info(codes.get(rc, f'알 수 없는 오류 (rc={rc})'))
        if rc == 0:
            client.subscribe(self.mqtt_topic)
            self.get_logger().info(f'구독: {self.mqtt_topic}')

    def on_message(self, client, userdata, msg):
        payload = msg.payload.decode('utf-8', errors='replace')

        if msg.topic == self.topic_base:
            room_id = 'default'
        else:
            room_id = msg.topic[len(self.topic_base):].lstrip('/') or 'default'

        match = READING_PATTERN.search(payload)
        scent = match.group(1) if match else None
        percent = float(match.group(2)) if match else None

        data = {
            'room_id': room_id,
            'topic': msg.topic,
            'payload': payload,
            'scent': scent,
            'percent': percent,
            'stamp': datetime.now(timezone.utc).isoformat(),
        }
        self.publisher_.publish(String(data=json.dumps(data)))

    def on_disconnect(self, client, userdata, rc):
        if rc != 0:
            self.get_logger().warn(f'예기치 않은 연결 끊김 (rc={rc}), 재연결 시도 중...')

    def destroy_node(self):
        self.client.loop_stop()
        self.client.disconnect()
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = MqttSubNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
