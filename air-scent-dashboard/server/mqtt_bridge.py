#!/usr/bin/env python3
"""HiveMQ 공기질 분류 MQTT → HTTP 브리지 (방/와드별)."""

from __future__ import annotations

import json
import os
import random
import re
import ssl
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    import paho.mqtt.client as mqtt
except ImportError:  # pragma: no cover
    mqtt = None  # type: ignore

PORT = int(os.environ.get("MQTT_BRIDGE_PORT", "5175"))
MQTT_BROKER = os.environ.get(
    "MQTT_BROKER", "e694bf432d234100929a519ed0bbec82.s1.eu.hivemq.cloud"
)
MQTT_PORT = int(os.environ.get("MQTT_PORT", "8883"))
MQTT_USER = os.environ.get("MQTT_USER", "third_impact")
MQTT_PASS = os.environ.get("MQTT_PASS", "Ti000000")
# GDM과 동일: sensor/air_quality 또는 sensor/air_quality/<room_id>
MQTT_TOPIC = os.environ.get("MQTT_TOPIC", "sensor/air_quality/#")
MQTT_BROKER_FLORAL = os.environ.get(
    "MQTT_BROKER_FLORAL",
    "e694bf432d234100929a519ed0bbec82.s1.eu.hivemq.cloud",
)

PAYLOAD_RE = re.compile(r"([A-Za-z가-힣_][A-Za-z가-힣_ ]*?)\s*\(\s*(\d+(?:\.\d+)?)\s*%\s*\)")
WOODY_RE = re.compile(r"woody|우드", re.IGNORECASE)
FRESH_RE = re.compile(r"fresh|clean|무취|청정|air", re.IGNORECASE)
LAVENDER_RE = re.compile(r"lavender|라벤더|floral|플로럴|odor", re.IGNORECASE)
MUSK_RE = re.compile(r"musk|머스크", re.IGNORECASE)

latest_lock = threading.Lock()
latest: dict = {
    "raw": None,
    "label": None,
    "confidence": None,
    "is_woody": False,
    "tone": "unknown",
    "received_at": None,
    "room_id": "default",
}
rooms: dict[str, dict] = {}
mqtt_connected = False
mqtt_error: str | None = None
mqtt_brokers_up: set[str] = set()
TOPIC_BASE = MQTT_TOPIC.split("/#")[0].rstrip("/")
MAX_LOGS = 48
event_logs: deque[dict] = deque(maxlen=MAX_LOGS)
_log_seq = 0


def _append_log_locked(kind: str, text: str) -> None:
    global _log_seq
    _log_seq += 1
    event_logs.append(
        {
            "id": _log_seq,
            "kind": kind,
            "text": text,
            "time": datetime.now().strftime("%H:%M:%S"),
        }
    )


def append_log(kind: str, text: str) -> None:
    with latest_lock:
        _append_log_locked(kind, text)


LIVING_ROOM_IDS = ("default", "room_1", "room1")
DEMO_LABELS = {
    "fresh": "Fresh Air",
    "fresh air": "Fresh Air",
    "air": "Fresh Air",
    "odor": "Floral",
    "floral": "Floral",
    "lavender": "Floral",
    "citrus": "woody",
    "woody": "woody",
    "musk": "musk",
    "우디": "musk",
}
demo_generation = 0
demo_override: dict | None = None
TTS_URL = os.environ.get("TTS_SPEAK_URL", "http://127.0.0.1:5180/api/tts/speak")
TTS_STOP_URL = os.environ.get("TTS_STOP_URL", "http://127.0.0.1:5180/api/tts/stop")
_odor_tts_lock = threading.Lock()
_odor_tts_generation = 0
_live_odor_active = False


def classify_tone(label: str | None) -> str:
    if not label:
        return "unknown"
    # Fresh Air / air 단독을 woody보다 먼저
    if re.search(r"fresh\s*air|fresh|clean|무취|청정|(?<![A-Za-z])air(?![A-Za-z])", label, re.IGNORECASE):
        return "fresh"
    if WOODY_RE.search(label):
        return "woody"
    if LAVENDER_RE.search(label):
        return "lavender"
    if MUSK_RE.search(label):
        return "musk"
    return "other"


def parse_payload(payload: str) -> dict:
    text = payload.strip()
    # Fresh Air(100%) / woody(98%) / 라벨 ( 90 % )
    match = re.search(
        r"(.+?)\s*\(\s*(\d+(?:\.\d+)?)\s*%\s*\)\s*$",
        text,
    )

    if match:
        label = match.group(1).strip()
        confidence = float(match.group(2))
        if confidence == int(confidence):
            confidence = int(confidence)
    else:
        label = text
        confidence = None

    if re.fullmatch(r"air|fresh(?:\s*air)?", label, re.IGNORECASE):
        label = "Fresh Air"

    tone = classify_tone(label)
    return {
        "raw": text,
        "label": label,
        "confidence": confidence,
        "is_woody": tone == "woody",
        "tone": tone,
    }


def room_id_from_topic(topic: str) -> str:
    if topic == TOPIC_BASE:
        return "default"
    suffix = topic[len(TOPIC_BASE) :].lstrip("/") if topic.startswith(TOPIC_BASE) else ""
    return suffix or "default"


def _demo_entry(label: str, confidence: int) -> dict:
    tone = classify_tone(label)
    stamp = datetime.now(timezone.utc).isoformat()
    raw = f"{label} ({confidence}%)"
    return {
        "raw": raw,
        "label": label,
        "confidence": confidence,
        "is_woody": tone == "woody",
        "tone": tone,
        "room_id": "default",
        "topic": "demo/living",
        "received_at": stamp,
        "demo": True,
    }


def _apply_demo(label: str, confidence: int) -> dict:
    global latest, demo_override
    entry = _demo_entry(label, confidence)
    demo_override = dict(entry)
    latest = dict(entry)
    for room_id in LIVING_ROOM_IDS:
        copied = dict(entry)
        copied["room_id"] = room_id
        rooms[room_id] = copied
    _append_log_locked("demo", f"[시연] {entry['raw']}")
    return entry


def _ramp_steps(target: int) -> list[int]:
    goal = max(10, min(98, int(target)))
    peak = min(98, goal + random.randint(0, 4))
    fractions = (0.27, 0.45, 0.70, 1.0)
    steps: list[int] = []
    prev = 0
    remaining = len(fractions)
    for index, frac in enumerate(fractions):
        remaining -= 1
        if remaining == 0:
            value = peak
        else:
            value = int(round(peak * frac)) + random.randint(-3, 3)
            value = max(prev + 4, value)
            value = min(peak - remaining * 3, value)
        value = max(1, min(peak, value))
        steps.append(value)
        prev = value
    steps[-1] = peak
    return steps


def _ramp_demo(generation: int, label: str, steps: list[int]) -> None:
    for index, confidence in enumerate(steps):
        if index == 0:
            continue
        if generation != demo_generation:
            return
        time.sleep(random.uniform(0.75, 1.45))
        if generation != demo_generation:
            return
        with latest_lock:
            if generation != demo_generation:
                return
            _apply_demo(label, confidence)

    if generation != demo_generation:
        return
    with latest_lock:
        if generation != demo_generation:
            return
        _append_log_locked("demo", "[시연] 공기청정 및 발향 완료")


def start_living_demo(raw_label: str, target: int | None = None) -> dict:
    global demo_generation
    key = str(raw_label or "").strip().lower()
    label = DEMO_LABELS.get(key)
    if not label:
        compact = key.replace(" ", "")
        label = DEMO_LABELS.get(compact)
    if not label:
        raise ValueError("지원하지 않는 향입니다")

    steps = _ramp_steps(54 if target is None else target)
    with latest_lock:
        demo_generation += 1
        generation = demo_generation
        entry = _apply_demo(label, steps[0])

    worker = threading.Thread(
        target=_ramp_demo,
        args=(generation, label, steps),
        name="mqtt-demo-ramp",
        daemon=True,
    )
    worker.start()
    if classify_tone(label) == "lavender":
        global _live_odor_active
        with latest_lock:
            _live_odor_active = True
        _trigger_odor_tts()
    return entry


def _trigger_odor_tts() -> None:
    global _odor_tts_generation
    with _odor_tts_lock:
        _odor_tts_generation += 1
        tts_gen = _odor_tts_generation
    threading.Thread(
        target=_announce_living_odor,
        args=(tts_gen,),
        name="mqtt-odor-tts",
        daemon=True,
    ).start()


def _note_live_odor(is_odor: bool) -> bool:
    global _live_odor_active
    if not is_odor:
        _live_odor_active = False
        return False
    if _live_odor_active:
        return False
    _live_odor_active = True
    return True


def _speak_tts(text: str) -> bool:
    try:
        req = urllib.request.Request(
            TTS_URL,
            data=json.dumps({"text": text}).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=3).read()
        return True
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        print(f"[mqtt-bridge] TTS 실패: {exc}", file=sys.stderr)
        return False


def _stop_tts() -> None:
    try:
        req = urllib.request.Request(TTS_STOP_URL, data=b"{}", method="POST")
        urllib.request.urlopen(req, timeout=2).read()
    except (urllib.error.URLError, TimeoutError, OSError):
        return


def _announce_living_odor(tts_gen: int) -> None:
    if tts_gen != _odor_tts_generation:
        return
    _stop_tts()
    if tts_gen != _odor_tts_generation:
        return
    lines = (
        "거실에서 악취를 감지했습니다.",
    )
    for text in lines:
        if tts_gen != _odor_tts_generation:
            return
        if not _speak_tts(text):
            return


def clear_living_demo() -> None:
    global demo_generation, demo_override, latest
    with latest_lock:
        was_demo = demo_override is not None or any(
            isinstance(rooms.get(room_id), dict) and rooms[room_id].get("demo")
            for room_id in LIVING_ROOM_IDS
        )
        demo_generation += 1
        demo_override = None
        live = None
        for key in ("live:default", "live:room_1", "live:room1"):
            entry = rooms.get(key)
            if entry and not entry.get("demo"):
                live = dict(entry)
                live["demo"] = False
                break
        if live:
            latest = live
            for room_id in LIVING_ROOM_IDS:
                copied = dict(live)
                copied["room_id"] = room_id
                rooms[room_id] = copied
        else:
            if isinstance(latest, dict):
                latest = {**latest, "demo": False}
            for room_id in LIVING_ROOM_IDS:
                entry = rooms.get(room_id)
                if isinstance(entry, dict):
                    rooms[room_id] = {**entry, "demo": False}
        if was_demo:
            _append_log_locked("demo", "[시연] 해제")
    _stop_tts()


def snapshot_air_quality() -> tuple[dict, dict[str, dict], list[dict]]:
    with latest_lock:
        snapshot = dict(latest)
        room_snapshot = {key: dict(value) for key, value in rooms.items()}
        logs = [
            item
            for item in event_logs
            if item.get("kind") in {"mqtt", "demo"}
        ]
        if demo_override:
            entry = dict(demo_override)
            snapshot.update(entry)
            for room_id in LIVING_ROOM_IDS:
                copied = dict(entry)
                copied["room_id"] = room_id
                room_snapshot[room_id] = copied
    return snapshot, room_snapshot, logs


def _broker_tag(userdata) -> str:
    if isinstance(userdata, dict):
        return str(userdata.get("tag") or userdata.get("broker") or "main")
    return "main"


def on_connect(client, userdata, flags, rc) -> None:
    global mqtt_connected, mqtt_error

    tag = _broker_tag(userdata)
    if rc == 0:
        mqtt_brokers_up.add(tag)
        mqtt_connected = True
        mqtt_error = None
        client.subscribe(MQTT_TOPIC)
        print(f"[mqtt-bridge] 구독: {MQTT_TOPIC} ({tag})", file=sys.stderr)
        return

    mqtt_brokers_up.discard(tag)
    mqtt_connected = bool(mqtt_brokers_up)
    mqtt_error = f"MQTT 연결 실패 (rc={rc}, {tag})"
    print(f"[mqtt-bridge] {mqtt_error}", file=sys.stderr)


_retry_log_at: dict[str, float] = {}


def _log_retry(tag: str, message: str) -> None:
    now = time.monotonic()
    last = _retry_log_at.get(tag, 0.0)
    if last and now - last < 30:
        return
    _retry_log_at[tag] = now
    print(message, file=sys.stderr)


def on_connect_fail(client, userdata) -> None:
    global mqtt_connected, mqtt_error

    tag = _broker_tag(userdata)
    mqtt_brokers_up.discard(tag)
    mqtt_connected = bool(mqtt_brokers_up)
    mqtt_error = f"MQTT 연결 실패, 재시도 중 ({tag})"
    _log_retry(tag, f"[mqtt-bridge] {mqtt_error}")


def on_disconnect(client, userdata, rc) -> None:
    global mqtt_connected, mqtt_error

    tag = _broker_tag(userdata)
    mqtt_brokers_up.discard(tag)
    mqtt_connected = bool(mqtt_brokers_up)
    if rc != 0:
        mqtt_error = f"MQTT 연결 끊김, 재시도 중 (rc={rc}, {tag})"
        _log_retry(tag, f"[mqtt-bridge] {mqtt_error}")


def on_message(client, userdata, msg) -> None:
    global latest

    payload = msg.payload.decode("utf-8", errors="replace")
    parsed = parse_payload(payload)
    room_id = room_id_from_topic(msg.topic)
    stamp = datetime.now(timezone.utc).isoformat()
    entry = {
        **parsed,
        "room_id": room_id,
        "topic": msg.topic,
        "received_at": stamp,
    }
    is_odor = parsed.get("tone") == "lavender"
    start_tts = False

    with latest_lock:
        _append_log_locked("mqtt", payload)
        if demo_override and room_id in LIVING_ROOM_IDS:
            rooms[f"live:{room_id}"] = entry
            print(f"[mqtt-bridge] ← [{room_id}] {payload} (시연 중 유지)", file=sys.stderr)
        else:
            rooms[room_id] = entry
            latest = dict(entry)
            print(f"[mqtt-bridge] ← [{room_id}] {payload}", file=sys.stderr)
        if room_id in LIVING_ROOM_IDS:
            start_tts = _note_live_odor(is_odor)

    if start_tts:
        print("[mqtt-bridge] 악취 TTS 안내", file=sys.stderr)
        _trigger_odor_tts()


def _run_mqtt_client(broker: str, tag: str) -> None:
    global mqtt_error

    if mqtt is None:
        mqtt_error = "paho-mqtt 미설치 (pip install paho-mqtt)"
        print(f"[mqtt-bridge] {mqtt_error}", file=sys.stderr)
        return

    # 동일 client_id 중복 시 HiveMQ가 한쪽을 끊음 → pid·브로커로 고유화
    client = mqtt.Client(
        client_id=f"Dashboard_MQTT_Bridge_{os.getpid()}_{tag}",
        protocol=mqtt.MQTTv311,
    )
    client.user_data_set({"broker": broker, "tag": tag})
    client.username_pw_set(MQTT_USER, MQTT_PASS)
    client.tls_set(cert_reqs=ssl.CERT_NONE)
    client.tls_insecure_set(True)
    client.on_connect = on_connect
    client.on_connect_fail = on_connect_fail
    client.on_disconnect = on_disconnect
    client.on_message = on_message
    # DNS가 부팅 직후 실패해도 connect()에서 스레드가 죽지 않게 비동기 연결 + 백오프
    client.reconnect_delay_set(min_delay=2, max_delay=30)
    client.connect_async(broker, MQTT_PORT, keepalive=60)
    client.loop_forever(retry_first_connection=True)


def start_mqtt_client() -> None:
    brokers = [(MQTT_BROKER, "main")]
    if MQTT_BROKER_FLORAL and MQTT_BROKER_FLORAL != MQTT_BROKER:
        brokers.append((MQTT_BROKER_FLORAL, "floral"))

    for broker, tag in brokers[1:]:
        worker = threading.Thread(
            target=_run_mqtt_client,
            args=(broker, tag),
            name=f"mqtt-{tag}",
            daemon=True,
        )
        worker.start()

    _run_mqtt_client(*brokers[0])


class MqttBridgeHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/api/mqtt/health":
            self._send_json(
                200,
                {
                    "ok": True,
                    "connected": mqtt_connected,
                    "topic": MQTT_TOPIC,
                    "broker": MQTT_BROKER,
                    "error": mqtt_error,
                },
            )
            return

        if self.path == "/api/mqtt/air-quality":
            snapshot, room_snapshot, logs = snapshot_air_quality()
            self._send_json(
                200,
                {
                    "ok": True,
                    "connected": mqtt_connected or bool(snapshot.get("demo")),
                    "mqtt_connected": mqtt_connected,
                    "error": mqtt_error,
                    "rooms": room_snapshot,
                    "logs": logs,
                    **snapshot,
                },
            )
            return

        self._send_json(404, {"ok": False})

    def do_POST(self) -> None:  # noqa: N802
        if self.path not in {"/api/mqtt/demo-scent", "/api/mqtt/air-quality/demo"}:
            self._send_json(404, {"ok": False})
            return

        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            self._send_json(400, {"ok": False, "error": "잘못된 JSON입니다"})
            return

        label = payload.get("label")
        if label is None or str(label).strip().lower() in {"", "off", "clear", "live", "real"}:
            clear_living_demo()
            self._send_json(200, {"ok": True, "cleared": True})
            return

        target = payload.get("target")
        if target is None:
            target = payload.get("intensity")
        try:
            target_n = int(float(target)) if target is not None else 54
        except (TypeError, ValueError):
            target_n = 54
        try:
            entry = start_living_demo(str(label), target_n)
        except ValueError as exc:
            self._send_json(400, {"ok": False, "error": str(exc)})
            return

        self._send_json(200, {"ok": True, "demo": True, **entry})


def main() -> None:
    mqtt_thread = threading.Thread(target=start_mqtt_client, daemon=True)
    mqtt_thread.start()

    server = ThreadingHTTPServer(("127.0.0.1", PORT), MqttBridgeHandler)
    print(
        f"[mqtt-bridge] http://127.0.0.1:{PORT} ← mqtt://{MQTT_BROKER}:{MQTT_PORT}/{MQTT_TOPIC}",
        file=sys.stderr,
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
