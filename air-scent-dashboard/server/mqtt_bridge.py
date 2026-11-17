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
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tts_speak import ODOR_DEPART, scent_weak, speak  # noqa: E402

try:
    import paho.mqtt.client as mqtt
except ImportError:  # pragma: no cover
    mqtt = None  # type: ignore

PORT = int(os.environ.get("MQTT_BRIDGE_PORT", "5175"))
MQTT_BROKER = os.environ.get("MQTT_BROKER", "")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "8883"))
MQTT_USER = os.environ.get("MQTT_USER", "")
MQTT_PASS = os.environ.get("MQTT_PASS", "")
# GDM과 동일: sensor/air_quality 또는 sensor/air_quality/<room_id>
MQTT_TOPIC = os.environ.get("MQTT_TOPIC", "sensor/air_quality/#")
MQTT_BROKER_LAVENDER = os.environ.get("MQTT_BROKER_LAVENDER", MQTT_BROKER)

PAYLOAD_RE = re.compile(r"([A-Za-z가-힣_][A-Za-z가-힣_ ]*?)\s*\(\s*(\d+(?:\.\d+)?)\s*%\s*\)")
FRESH_RE = re.compile(r"fresh|clean|무취|청정|air", re.IGNORECASE)
# 센서(ESP32 BME688)는 학습 모델의 클래스 이름을 그대로 보내는데, 이 이름이 실제 향과 어긋나 있다
# (ward_bme688 학습 라벨 뒤바뀜). 센서를 다시 굽기 전까지 옛 라벨은 여기서만 해석한다.
#   "woody" · "citrus" → orange   "musk" → mint
#   "floral" · "odor" · "악취" → odor    "lavender" · "라벤더" → lavender
# 프론트 src/utils/sensorLabels.js 와 같은 규칙. 새 이름(orange / mint / lavender)도 알아듣는다.
SENSOR_TONE_RULES = (
    ("orange", re.compile(r"orange|오렌지|woody|citrus", re.IGNORECASE)),
    ("odor", re.compile(r"odor|악취|floral|vinegar|식초", re.IGNORECASE)),
    ("lavender", re.compile(r"lavender|라벤더", re.IGNORECASE)),
    ("mint", re.compile(r"mint|민트|musk", re.IGNORECASE)),
)

latest_lock = threading.Lock()
latest: dict = {
    "raw": None,
    "label": None,
    "confidence": None,
    "is_orange": False,
    "tone": "unknown",
    "received_at": None,
    "room_id": "default",
}
rooms: dict[str, dict] = {}
mqtt_connected = False
mqtt_error: str | None = None
mqtt_brokers_up: set[str] = set()
TOPIC_BASE = MQTT_TOPIC.split("/#")[0].rstrip("/")
# 새 와드(온디바이스 판정) — 시연 탭 수신 로그에만 쓴다. 옛 sensor/air_quality 흐름(방 상태·악취 안내)은 그대로.
WARD_TOPIC = os.environ.get("WARD_TOPIC", "diffuser/+/inference_ward")
WARD_STATE_KO = {"WARMUP": "준비 중", "CLEAN": "깨끗함", "SCENT": "향기", "MALODOR": "악취",
                 "UNKNOWN": "모르는 냄새", "HOLD": "약함"}
WARD_LABEL_KO = {"Peppermint": "페퍼민트", "Lavender": "라벤더", "Orange": "오렌지", "Vinegar": "식초"}
WARD_SCENT_KO = {"peppermint": "페퍼민트", "mint": "페퍼민트", "lavender": "라벤더", "orange": "오렌지"}
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
# 더보기 시연 버튼 id → 시연 중 보여 줄 라벨 (MQTT 로 내보내지 않고 대시보드 안에서만 쓴다)
DEMO_LABELS = {
    "fresh": "무향",
    "fresh air": "무향",
    "air": "무향",
    "무향": "무향",
    "odor": "odor",
    "orange": "Orange",
    "mint": "Mint",
    "peppermint": "Mint",
    "페퍼민트": "Mint",
    "lavender": "Lavender",
    "라벤더": "Lavender",
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
    # Fresh Air / air 단독을 향 라벨보다 먼저
    if re.search(r"fresh\s*air|fresh|clean|무취|무향|청정|(?<![A-Za-z])air(?![A-Za-z])", label, re.IGNORECASE):
        return "fresh"
    for tone, pattern in SENSOR_TONE_RULES:
        if pattern.search(label):
            return tone
    return "other"


def parse_payload(payload: str) -> dict:
    text = payload.strip()
    # Fresh Air(100%) / mint(98%) / 라벨 ( 90 % )
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
        "is_orange": tone == "orange",
        "tone": tone,
    }


def room_id_from_topic(topic: str) -> str:
    if topic == TOPIC_BASE:
        return "default"
    suffix = topic[len(TOPIC_BASE) :].lstrip("/") if topic.startswith(TOPIC_BASE) else ""
    return suffix or "default"


def _demo_entry(label: str, confidence: int | None) -> dict:
    tone = classify_tone(label)
    stamp = datetime.now(timezone.utc).isoformat()
    raw = label if confidence is None else f"{label} ({confidence}%)"
    return {
        "raw": raw,
        "label": label,
        "confidence": confidence,
        "is_orange": tone == "orange",
        "tone": tone,
        "room_id": "default",
        "topic": "demo/living",
        "received_at": stamp,
        "demo": True,
    }


def _apply_demo(label: str, confidence: int | None, announce: bool = True) -> dict:
    global latest, demo_override
    entry = _demo_entry(label, confidence)
    demo_override = dict(entry)
    latest = dict(entry)
    for room_id in LIVING_ROOM_IDS:
        copied = dict(entry)
        copied["room_id"] = room_id
        rooms[room_id] = copied
    if announce:
        _append_log_locked("demo", f"[시연] {entry['raw']}")
    return entry


# 시연 향: 17~18에서 30초 동안 92~97까지 오른 뒤, 200초 동안 70까지 내려간다.
# 70 아래도 분사가 시작될 때까지 계속 내려가고, 분사가 시작되면 다시 올라간다. 그 뒤 200초 하강을 반복한다.
DEMO_RISE_SEC = 30
DEMO_FALL_SEC = 200
DEMO_FALL_TO = 70
DEMO_FLOOR = 10
DEMO_TICK_SEC = 1.0
# 악취 시연만: 공기청정기가 켜지면 30초 동안 15%까지 내린다. 향 버튼 곡선은 그대로.
DEMO_ODOR_CLEAR_SEC = 30
DEMO_ODOR_CLEAR_TO = 15
NAV_UI_URL = os.environ.get("NAV_UI_URL", "http://127.0.0.1:5179/api/nav/ui")
NAV_GOAL_URL = os.environ.get("NAV_GOAL_URL", "http://127.0.0.1:5179/api/nav/goal")
# 이 라벨만 70%에 닿으면 그 향으로 발향하러 출발한다.
DEMO_SPRAY_CHANNEL = {"Orange": "orange", "Lavender": "lavender", "Mint": "mint"}
_demo_rise = threading.Event()
_demo_purify = threading.Event()
_demo_scent = threading.Event()
_demo_scent_name: str | None = None


def _smoothstep(amount: float) -> float:
    t = max(0.0, min(1.0, amount))
    return t * t * (3.0 - 2.0 * t)


def _demo_bounds() -> tuple[int, int]:
    return random.randint(17, 18), random.randint(92, 97)


def _nav_json(url: str, payload: dict, method: str) -> dict | None:
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            method=method,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=3) as response:
            body = response.read().decode("utf-8")
        return json.loads(body) if body else {"ok": True}
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        print(f"[mqtt-bridge] 주행 요청 실패: {exc}", file=sys.stderr)
        return None


def _depart_to_spray(channel: str) -> bool:
    """70%가 되면 그 채널로 발향부터 하도록 표시하고, 저장된 와드 목표로 출발한다."""
    marked = _nav_json(
        NAV_UI_URL,
        {"sprayFirst": True, "sprayChannel": channel},
        "PUT",
    )
    if not marked or marked.get("ok") is False:
        return False
    sent = _nav_json(NAV_GOAL_URL, {"x": 0, "y": 0, "yaw": 0, "frame_id": "map"}, "POST")
    if sent and sent.get("ok"):
        speak(scent_weak(channel))
        return True
    _nav_json(NAV_UI_URL, {"sprayFirst": False, "sprayChannel": None}, "PUT")
    return False


def _depart_to_purify() -> bool:
    """악취는 발향부터가 아니라, 도착 후 청정부터 하도록 표시를 지우고 출발한다."""
    cleared = _nav_json(
        NAV_UI_URL,
        {"sprayFirst": False, "sprayChannel": None},
        "PUT",
    )
    if not cleared or cleared.get("ok") is False:
        return False
    sent = _nav_json(NAV_GOAL_URL, {"x": 0, "y": 0, "yaw": 0, "frame_id": "map"}, "POST")
    ok = bool(sent and sent.get("ok"))
    if ok:
        speak(ODOR_DEPART)
    return ok


def _dispatch_demo(channel: str | None) -> None:
    sent = _depart_to_spray(channel) if channel else _depart_to_purify()
    with latest_lock:
        if sent:
            action = "발향" if channel else "청정"
            _append_log_locked("demo", f"[시연] {DEMO_FALL_TO}% — 와드로 출발해 {action}")
        else:
            _append_log_locked("demo", f"[시연] {DEMO_FALL_TO}% — 출발하지 못했습니다")


def _run_demo_curve(
    generation: int,
    label: str,
    start: int,
    peak: int,
    depart: bool,
    channel: str | None,
) -> None:
    """향: 오름 30초 → 70까지 200초 → 분사 때 다시 상승 → 다시 200초 하강.
    악취: 청정기가 켜지면 30초 동안 15%로 내린 뒤, 이어서 뿌린 향은
    95~96%까지 오른 다음 같은 하강으로 70 아래까지 내려간다."""
    level = float(start)
    shown = start
    phase = "rise"
    rise_from = float(start)
    rise_i = 0
    fall_from = float(peak)
    fall_i = 0
    clear_from = float(start)
    clear_i = 0
    dispatched = False
    tick = 0
    odor = label == "odor"
    shown_label = label

    while True:
        rising_now = _demo_rise.wait(DEMO_TICK_SEC)
        purify_now = _demo_purify.is_set()
        scent_now = _demo_scent.is_set()
        if generation != demo_generation:
            return
        if scent_now:
            _demo_scent.clear()
            next_label = _demo_scent_name
            if next_label:
                shown_label = next_label
                odor = False
                # 이미 출동해 뿌린 뒤라 70%에서 다시 출발하지 않는다. 숫자만 내려간다.
                depart = False
                phase = "rise"
                rise_from = level
                rise_i = 0
                peak = random.randint(95, 96)
                dispatched = False
                with latest_lock:
                    _append_log_locked(
                        "demo",
                        f"[시연] 15% 이후 {shown_label} 발향 — {peak}%까지 오른 뒤 70% 아래로 하강",
                    )
        if purify_now and odor and phase not in ("clear", "hold"):
            _demo_purify.clear()
            phase = "clear"
            clear_from = level
            clear_i = 0
            with latest_lock:
                _append_log_locked(
                    "demo",
                    f"[시연] 청정기 가동 — 악취 {DEMO_ODOR_CLEAR_SEC}초 동안 {DEMO_ODOR_CLEAR_TO}%",
                )
        elif purify_now:
            _demo_purify.clear()
        if rising_now:
            _demo_rise.clear()
            if not odor:
                phase = "rise"
                rise_from = level
                rise_i = 0
                peak = random.randint(92, 97)
                if peak <= level:
                    peak = 97 if level >= 97 else int(round(level)) + 1
                dispatched = False
                with latest_lock:
                    _append_log_locked("demo", f"[시연] 분사 시작 — {label} 다시 상승")

        tick += 1
        if phase == "clear":
            clear_i += 1
            if clear_i >= DEMO_ODOR_CLEAR_SEC:
                level = float(DEMO_ODOR_CLEAR_TO)
                phase = "hold"
            else:
                level = clear_from + (DEMO_ODOR_CLEAR_TO - clear_from) * _smoothstep(
                    clear_i / DEMO_ODOR_CLEAR_SEC
                )
        elif phase == "hold":
            level = float(DEMO_ODOR_CLEAR_TO)
        elif phase == "rise":
            rise_i += 1
            if rise_i >= DEMO_RISE_SEC:
                level = float(peak)
                phase = "fall"
                fall_from = level
                fall_i = 0
            else:
                risen = rise_from + (peak - rise_from) * _smoothstep(rise_i / DEMO_RISE_SEC)
                level = max(level, min(float(peak), risen))
        else:
            rate = max((fall_from - DEMO_FALL_TO) / DEMO_FALL_SEC, 0.05)
            level = max(float(DEMO_FLOOR), level - rate)
            fall_i += 1
            if level <= DEMO_FALL_TO:
                phase = "drift"
            if depart and not dispatched and level <= DEMO_FALL_TO:
                dispatched = True
                _dispatch_demo(channel)
                if generation != demo_generation:
                    return

        nxt = int(round(level))
        milestone = tick % 15 == 0 or (phase == "fall" and fall_i == 0)
        shown = nxt
        with latest_lock:
            if generation != demo_generation:
                return
            _apply_demo(shown_label, shown, announce=milestone)


def start_living_demo(raw_label: str, target: int | None = None, keep: bool = False) -> dict:
    global demo_generation
    del target  # 저장해 둔 목표 퍼센트 대신 아래 곡선으로 올린다.
    key = str(raw_label or "").strip().lower()
    label = DEMO_LABELS.get(key)
    if not label:
        compact = key.replace(" ", "")
        label = DEMO_LABELS.get(compact)
    if not label:
        raise ValueError("지원하지 않는 향입니다")

    if keep:
        with latest_lock:
            current = dict(demo_override) if isinstance(demo_override, dict) else None
        if current and current.get("demo") and current.get("label") == label:
            return current

    start, peak = _demo_bounds()
    channel = DEMO_SPRAY_CHANNEL.get(label)
    depart = bool(channel) or label == "odor"
    unscented = label == "무향"
    _demo_rise.clear()
    _demo_purify.clear()
    _demo_scent.clear()
    with latest_lock:
        demo_generation += 1
        generation = demo_generation
        entry = _apply_demo(label, None if unscented else start)

    if unscented:
        return entry

    worker = threading.Thread(
        target=_run_demo_curve,
        args=(generation, label, start, peak, depart, channel),
        name="mqtt-demo-curve",
        daemon=True,
    )
    worker.start()
    if re.search(r"odor|악취|vinegar|식초", label, re.IGNORECASE):
        global _live_odor_active
        with latest_lock:
            _live_odor_active = True
    return entry


def signal_demo_scent(raw_label: str) -> dict | None:
    """악취가 15%가 된 뒤, 설정된 향으로 95~96%까지 올린 다음 70% 아래로 내린다."""
    global _demo_scent_name
    key = str(raw_label or "").strip().lower()
    label = DEMO_LABELS.get(key) or DEMO_LABELS.get(key.replace(" ", ""))
    if not label or label in ("odor", "무향"):
        return None
    _demo_scent_name = label
    _demo_scent.set()
    with latest_lock:
        return dict(demo_override) if isinstance(demo_override, dict) else None


def signal_demo_purify() -> dict | None:
    """악취 시연 중에 공기청정기가 켜지면 30초 동안 15%로 내린다. 다른 향은 건드리지 않는다."""
    with latest_lock:
        current = dict(demo_override) if isinstance(demo_override, dict) else None
    if not current or not current.get("demo") or current.get("label") != "odor":
        return current
    _demo_purify.set()
    return current


def signal_demo_rise(raw_label: str | None) -> dict | None:
    """분사가 시작되면 지금 시연 퍼센트를 다시 올린다. 다른 향이면 그 향 시연을 새로 켠다."""
    label = None
    if raw_label:
        key = str(raw_label).strip().lower()
        label = DEMO_LABELS.get(key) or DEMO_LABELS.get(key.replace(" ", ""))
        if not label:
            raise ValueError("지원하지 않는 향입니다")
    with latest_lock:
        current = dict(demo_override) if isinstance(demo_override, dict) else None
    if current and current.get("demo") and (label is None or current.get("label") == label) and current.get("label") != "무향":
        if current.get("label") == "odor":
            return current
        _demo_rise.set()
        return current
    if not raw_label or label == "무향":
        return current
    return start_living_demo(raw_label)


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
        ODOR_DEPART,
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
        if WARD_TOPIC:
            client.subscribe(WARD_TOPIC)
            print(f"[mqtt-bridge] 구독: {WARD_TOPIC} ({tag}, 시연 탭 로그)", file=sys.stderr)
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


def _ward_batt_text(d: dict) -> str:
    """와드 배터리 (11/15 펌웨어부터). 전압으로 어림한 % — 3.6V 아래면 충전 경고."""
    v = d.get("batt_v")
    if not isinstance(v, (int, float)) or v < 0:
        return ""
    warn = " ⚠ 충전 필요" if v < 3.6 else ""
    return f" · 배터리 {v:.2f}V ({d.get('batt_pct')}%){warn}"


def _ward_log_text(topic: str, payload: str) -> str:
    """와드 판정 JSON → 한 줄. 예: ward-01 · 악취(식초) · 세기 40% · 표 8/8 · 청정기 ON ◀ 출동"""
    try:
        d = json.loads(payload)
    except ValueError:
        return payload[:160]
    parts = topic.split("/")
    ward = str(d.get("device_id") or (parts[1] if len(parts) > 1 else "ward"))
    state = str(d.get("state") or "")
    head = WARD_STATE_KO.get(state, state)
    if state == "WARMUP":
        return f"{ward} · {head} (켜진 지 {d.get('uptime_s')}초)" + _ward_batt_text(d)
    name = WARD_LABEL_KO.get(str(d.get("label") or ""), "")
    if name and state == "SCENT":
        head += f"({name} 추정)"
    elif name and state == "MALODOR":
        head += f"({name})"
    text = f"{ward} · {head}"
    if d.get("strength_pct") is not None:
        text += f" · 세기 {d.get('strength_pct')}%"
    elif d.get("mag") is not None:
        text += f" · 세기 {float(d.get('mag')):.2f}"
    if d.get("votes") is not None:
        text += f" · 표 {d.get('votes')}/{d.get('n')}"
    if d.get("purifier") == "on":
        text += " · 청정기 ON"
    if d.get("spraying"):
        sp = str(d.get("spraying"))
        text += f" · 뿌리는 향 {WARD_SCENT_KO.get(sp.lower(), sp)}"
    if d.get("event") == "ON":
        text += " ◀ 출동"
    elif d.get("event") == "OFF":
        text += " ◀ 회복"
    return text + _ward_batt_text(d)


def on_message(client, userdata, msg) -> None:
    global latest

    payload = msg.payload.decode("utf-8", errors="replace")
    if msg.topic.endswith("/inference_ward"):
        append_log("mqtt", _ward_log_text(msg.topic, payload))
        return
    parsed = parse_payload(payload)
    room_id = room_id_from_topic(msg.topic)
    stamp = datetime.now(timezone.utc).isoformat()
    entry = {
        **parsed,
        "room_id": room_id,
        "topic": msg.topic,
        "received_at": stamp,
    }
    is_odor = parsed.get("tone") == "odor"
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
    global mqtt_error
    if not MQTT_BROKER:
        mqtt_error = "MQTT_BROKER 환경 변수가 설정되지 않았습니다 (.env.local 확인)"
        print(f"[mqtt-bridge] {mqtt_error}", file=sys.stderr)
        return

    brokers = [(MQTT_BROKER, "main")]
    if MQTT_BROKER_LAVENDER and MQTT_BROKER_LAVENDER != MQTT_BROKER:
        brokers.append((MQTT_BROKER_LAVENDER, "lavender"))

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
        if payload.get("scentRise"):
            entry = signal_demo_scent("" if label is None else str(label))
            if not entry:
                self._send_json(200, {"ok": True, "demo": False})
                return
            self._send_json(200, {"ok": True, "demo": True, "scentRise": True, **entry})
            return
        if payload.get("purify"):
            entry = signal_demo_purify()
            if not entry:
                self._send_json(200, {"ok": True, "demo": False})
                return
            self._send_json(200, {"ok": True, "demo": True, "purify": True, **entry})
            return
        if payload.get("rise"):
            try:
                entry = signal_demo_rise(None if label is None else str(label))
            except ValueError as exc:
                self._send_json(400, {"ok": False, "error": str(exc)})
                return
            if not entry:
                self._send_json(200, {"ok": True, "demo": False})
                return
            self._send_json(200, {"ok": True, "demo": True, "rise": True, **entry})
            return
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
            entry = start_living_demo(str(label), target_n, keep=bool(payload.get("keep")))
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
