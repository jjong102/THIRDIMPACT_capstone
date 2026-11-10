#!/usr/bin/env python3
"""ward_dispatch.py — 와드가 악취를 판정하면 /go_to_goal (UI Jetson, ros_nav_bridge 가 부른다)

  와드 MQTT diffuser/+/inference_ward  ──event:"ON" (악취·모르는 냄새 3라운드 연속, 와드가 판정)──▶  /go_to_goal
  → 주행 Jetson 이 저장 목표(= 와드)로 정밀 복귀 → /arrival_at_goal → arrival_sequence(청정·발향·전부 끔·/go_around)

출동하지 않는 경우 (로그에 이유를 남긴다):
  - 도착 후 청정·발향 순서가 도는 중 / 이미 복귀 중(isNavigating·missionPhase return) / 수동 조작 중
  - 마지막 출동 뒤 WARD_DISPATCH_COOLDOWN 초 안 (기본 60)
판정마다 화면 상태 wardState 에 최근 판정을 넣는다 (/api/nav/ui).
설정: WARD_DISPATCH(1/0) · WARD_DISPATCH_COOLDOWN · MQTT_BROKER/USER/PASS/PORT (.env.local) · ARRIVAL_LOG
"""

from __future__ import annotations

import json
import os
import ssl
import sys
import threading
import time


class WardDispatch:
    def __init__(self, dispatch, snapshot_ui, merge_ui, is_sequence_running, log_path=None):
        self.dispatch = dispatch                    # () -> bool  (publish_goal: /go_to_goal + 화면 상태)
        self.snapshot_ui = snapshot_ui
        self.merge_ui = merge_ui
        self.is_sequence_running = is_sequence_running
        self.enabled = os.environ.get("WARD_DISPATCH", "1").strip() not in ("0", "false", "no", "")
        self.cooldown = float(os.environ.get("WARD_DISPATCH_COOLDOWN", "60") or 60)
        self.log_path = log_path or os.environ.get("ARRIVAL_LOG", "/tmp/arrival_sequence.log")
        self._last_dispatch = 0.0
        self._lock = threading.Lock()
        self._client = None

    def log(self, msg: str) -> None:
        line = f"{time.strftime('%H:%M:%S')} [ward-dispatch] {msg}"
        print(line, file=sys.stderr, flush=True)
        try:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass

    # ------------------------------------------------------------ 시작
    def start(self) -> None:
        if not self.enabled:
            self.log("꺼짐 (WARD_DISPATCH=0)")
            return
        broker = os.environ.get("MQTT_BROKER", "")
        if not broker:
            self.log("MQTT_BROKER 없음 — 와드 출동 감시 안 함")
            return
        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            self.log("paho-mqtt 없음 — 와드 출동 감시 안 함")
            return
        c = mqtt.Client(client_id=f"WardDispatch_{os.getpid()}", protocol=mqtt.MQTTv311)
        c.username_pw_set(os.environ.get("MQTT_USER", ""), os.environ.get("MQTT_PASS", ""))
        c.tls_set(cert_reqs=ssl.CERT_NONE)
        c.tls_insecure_set(True)
        c.reconnect_delay_set(min_delay=2, max_delay=30)
        c.on_connect = self._on_connect
        c.on_message = self._on_message
        c.connect_async(broker, int(os.environ.get("MQTT_PORT", "8883")), 60)
        c.loop_start()
        self._client = c
        self.log(f"와드 출동 감시 시작 (쿨다운 {self.cooldown:.0f}초)")

    def _on_connect(self, client, _userdata, _flags, rc, *_rest):
        if rc == 0:
            client.subscribe("diffuser/+/inference_ward", qos=0)
            self.log("MQTT 연결 → diffuser/+/inference_ward 구독")
        else:
            self.log(f"MQTT 연결 실패 rc={rc}")

    # ------------------------------------------------------------ 판정 받기
    def _on_message(self, _client, _userdata, msg):
        try:
            d = json.loads(msg.payload.decode("utf-8", "replace"))
        except ValueError:
            return
        ward = str(d.get("device_id") or msg.topic.split("/")[1])
        try:
            self.merge_ui({"wardState": {
                "ward_id": ward, "state": d.get("state"), "label": d.get("label"),
                "label_note": d.get("label_note"), "strength_pct": d.get("strength_pct"),
                "purifier": d.get("purifier"), "event": d.get("event"), "spraying": d.get("spraying"),
                "uptime_s": d.get("uptime_s"), "rx_time": time.time()}})
        except Exception:  # noqa: BLE001
            pass
        if d.get("event") != "ON":
            return
        state = str(d.get("state") or "")
        if state not in ("MALODOR", "UNKNOWN"):
            self.log(f"{ward} event ON 이지만 state={state} — 무시")
            return
        threading.Thread(target=self._try_dispatch, args=(ward, d), daemon=True).start()

    def _try_dispatch(self, ward: str, d: dict) -> None:
        with self._lock:
            what = f"{ward} 악취 {d.get('label')} 세기 {d.get('strength_pct')}% (표 {d.get('votes')}/{d.get('n')})"
            now = time.time()
            ui = {}
            try:
                ui = self.snapshot_ui() or {}
            except Exception:  # noqa: BLE001
                pass
            if self.is_sequence_running():
                self.log(f"{what} — 도착 후 청정·발향 중이라 출동 안 함")
                return
            if ui.get("manualControl"):
                self.log(f"{what} — 수동 조작 중이라 출동 안 함")
                return
            if ui.get("isNavigating") or ui.get("missionPhase") == "return":
                self.log(f"{what} — 이미 복귀 중")
                return
            if now - self._last_dispatch < self.cooldown:
                self.log(f"{what} — 마지막 출동 {now - self._last_dispatch:.0f}초 전, 쿨다운 {self.cooldown:.0f}초")
                return
            ok = False
            try:
                ok = bool(self.dispatch())
            except Exception as exc:  # noqa: BLE001
                self.log(f"{what} — /go_to_goal 실패: {exc!r}")
                return
            if ok:
                self._last_dispatch = now
                self.log(f"{what} → /go_to_goal 출동")
            else:
                self.log(f"{what} — /go_to_goal 못 보냄 (ROS 준비 안 됨?)")
