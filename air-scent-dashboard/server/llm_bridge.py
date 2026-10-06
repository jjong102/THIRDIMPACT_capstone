#!/usr/bin/env python3
"""NVIDIA NIM Llama API → 향 추천 HTTP 브리지 + 규칙 검증 + MQTT 제어."""

from __future__ import annotations

import json
import os
import re
import ssl
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

try:
    import paho.mqtt.client as mqtt
except ImportError:  # pragma: no cover
    mqtt = None  # type: ignore

PORT = int(os.environ.get("LLM_BRIDGE_PORT", "5176"))
NIM_BASE_URL = os.environ.get(
    "NIM_BASE_URL", "https://integrate.api.nvidia.com/v1"
).rstrip("/")
NIM_MODEL = os.environ.get("NIM_MODEL", "google/gemma-4-31b-it")
if NIM_MODEL in {
    "meta/llama-3.1-8b-instruct",
    "meta/llama-3.1-70b-instruct",
    "meta/llama-3.2-3b-instruct",
    "nvidia/nemotron-3-nano-30b-a3b",
}:
    NIM_MODEL = "google/gemma-4-31b-it"
NVIDIA_API_KEY = os.environ.get("NVIDIA_API_KEY", "")

MQTT_BROKER = os.environ.get("MQTT_BROKER", "")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "8883"))
MQTT_USER = os.environ.get("MQTT_USER", "")
MQTT_PASS = os.environ.get("MQTT_PASS", "")
MQTT_CONTROL_TOPIC = os.environ.get(
    "MQTT_CONTROL_TOPIC", "device/fragrance/command"
)

MAX_DURATION_SECONDS = 60
MAX_CONTINUOUS_SPRAY_SECONDS = 30
MIN_COOLDOWN_SECONDS = 120
MIN_CARTRIDGE_PERCENT = 10

RECIPES_PATH = Path(__file__).with_name("scent_recipes.json")

mqtt_client = None
mqtt_connected = False
mqtt_error: str | None = None
mqtt_lock = threading.Lock()
_last_recipe_id: str | None = None


def load_recipes() -> list[dict]:
    with RECIPES_PATH.open(encoding="utf-8") as fp:
        return json.load(fp)


def recipe_by_id(recipe_id: str) -> dict | None:
    for recipe in load_recipes():
        if recipe["id"] == recipe_id:
            return recipe
    return None


def build_system_prompt(recipes: list[dict]) -> str:
    catalog = json.dumps(
        [
            {
                "id": r["id"],
                "name": r["displayName"],
                "tags": r["tags"],
                "description": r["description"],
            }
            for r in recipes
        ],
        ensure_ascii=False,
    )

    return f"""당신은 실내 향기 추천 AI입니다.

가장 중요한 기준은 사용자가 방금 한 말입니다. 날씨, 온도, 습도, 공기질은 말이 애매할 때만 참고하세요.
확인 질문(어떠세요, 이걸로 하실래요)은 하지 마세요.

## 우선순위
1. 사용자가 말한 장면·향·기분. 이게 있으면 환경은 무시하세요.
   - 꽃, 꽃밭, 정원, 라벤더, 쉬고 싶다, 피곤 → Floral(R002)
   - 숲, 나무, 우디, 따뜻, 차분 → Woody(R001)
   - 레몬, 시트러스, 상쾌하게 깨고 싶다 → Citrus(R003)
2. 말이 아주 애매할 때만 시간대: 아침/낮=Citrus, 저녁=Woody, 밤=Floral
3. 덥다고 해서 Citrus로 바꾸지 마세요. 꽃밭에서 쉬고 싶으면 더워도 Floral입니다.

## 규칙
- 반드시 아래 등록된 레시피 중 하나의 recipe_id만 선택하세요.
- 세 가지를 나열하며 고르라고 하지 마세요.
- intensity는 1~3 정수. 세기를 안 말하면 2.
- duration_seconds는 5~30 정수.
- reason은 한국어 한두 문장. 사용자가 한 말을 받아서 이 향을 고른 이유를 말하세요. 날씨/온도 이야기는 하지 마세요.
- 향 이름은 반드시 Woody, Floral, Citrus 영어만 쓰세요. 우디/플로럴/시트러스라고 쓰지 마세요.
- 예: "꽃밭에서 쉬고 싶다고 하셔서 Floral이 좋을 것 같아요."
- 확인 질문은 reason에 넣지 마세요.
- 실내에 우디 냄새가 강하게 있으면 Woody(R001)만 피하세요. 그래도 사용자 말이 꽃/휴식이면 Floral을 고르세요.

## 등록된 레시피
{catalog}

## 응답 형식 (JSON만, 다른 텍스트 금지)
{{
  "recipe_id": "R001",
  "intensity": 2,
  "duration_seconds": 15,
  "reason": "추천 이유"
}}"""


def build_user_prompt(payload: dict) -> str:
    user = payload.get("userInput", {})
    bme = payload.get("bme688", {})
    mqtt_data = payload.get("mqtt", {})

    return json.dumps(
        {
            "지시": "사용자 말을 최우선으로 향을 고르세요. 온도가 높아도 꽃밭/휴식 말이면 Floral입니다. 향 이름은 Woody/Floral/Citrus만 쓰세요.",
            "직전_추천_recipe_id": _last_recipe_id,
            "사용자_말": user.get("needNow")
            or user.get("desiredState")
            or user.get("currentMood")
            or "",
            "센서_참고만": {
                "공기상태": bme.get("airStatus"),
                "습도": bme.get("humidity"),
                "온도": bme.get("temperature"),
            },
            "냄새_분류_MQTT": {
                "라벨": mqtt_data.get("label"),
                "신뢰도": mqtt_data.get("confidence"),
                "우디_감지": mqtt_data.get("isWoody"),
            },
            "시간대": payload.get("timePeriod"),
        },
        ensure_ascii=False,
    )


def extract_json(text: str) -> dict:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence:
        text = fence.group(1).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", text)
        if not match:
            raise ValueError("LLM 응답에서 JSON을 찾을 수 없습니다.") from None
        return json.loads(match.group(0))


def call_nim_chat(messages: list[dict]) -> str:
    if not NVIDIA_API_KEY:
        raise RuntimeError("NVIDIA_API_KEY 환경 변수가 설정되지 않았습니다.")

    body = json.dumps(
        {
            "model": NIM_MODEL,
            "messages": messages,
            "temperature": 0.3,
            "max_tokens": 512,
        }
    ).encode("utf-8")

    request = urllib.request.Request(
        f"{NIM_BASE_URL}/chat/completions",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {NVIDIA_API_KEY}",
        },
        method="POST",
    )

    with urllib.request.urlopen(request, timeout=60) as response:
        payload = json.loads(response.read().decode("utf-8"))

    return payload["choices"][0]["message"]["content"]


def _blocked_recipe_ids(payload: dict) -> set[str]:
    blocked: set[str] = set()
    bme = payload.get("bme688", {})
    mqtt_data = payload.get("mqtt", {})
    humidity = bme.get("humidity")
    if mqtt_data.get("isWoody"):
        blocked.add("R001")
    if humidity is not None and humidity < 30:
        blocked.add("R003")
    return blocked


def _pick_recipe_id(preferred: str, blocked: set[str]) -> str:
    global _last_recipe_id
    order = [preferred, "R003", "R001", "R002"]
    unique: list[str] = []
    for recipe_id in order:
        if recipe_id in blocked or recipe_id in unique:
            continue
        unique.append(recipe_id)
    if not unique:
        unique = ["R002"]
    if _last_recipe_id in unique and len(unique) > 1:
        unique = [rid for rid in unique if rid != _last_recipe_id]
    chosen = unique[0]
    _last_recipe_id = chosen
    return chosen


def _default_recipe_by_time(time_period: str) -> str:
    period = str(time_period or "").lower()
    if any(k in period for k in ["morning", "afternoon", "아침", "오전", "낮", "오후"]):
        return "R003"
    if any(k in period for k in ["evening", "저녁"]):
        return "R001"
    if any(k in period for k in ["night", "밤", "심야"]):
        return "R002"
    return "R003"


def _user_spoken(payload: dict) -> str:
    user = payload.get("userInput", {})
    return str(
        user.get("needNow") or user.get("desiredState") or user.get("currentMood") or ""
    )


def infer_recipe_from_speech(spoken: str) -> str | None:
    text = str(spoken or "")
    if not text.strip():
        return None

    if any(
        k in text
        for k in ["꽃밭", "꽃길", "꽃", "플로럴", "라벤더", "장미", "벚꽃", "정원", "수국"]
    ):
        return "R002"
    if any(k in text for k in ["우디", "우드", "숲속", "숲", "나무", "편백", "모닥불", "캠핑"]):
        return "R001"
    if any(k in text for k in ["시트러스", "레몬", "오렌지", "자몽", "라임", "유자"]):
        return "R003"
    if any(
        k in text
        for k in ["쉬고", "휴식", "수면", "피곤", "졸려", "스트레스", "불안", "편하게"]
    ):
        return "R002"
    if any(k in text for k in ["따뜻", "차분", "안정", "포근", "묵직"]):
        return "R001"
    if any(k in text for k in ["상쾌", "활력", "집중", "깨고", "산뜻"]):
        return "R003"
    if any(k in text for k in ["더워", "더움", "답답", "탁해"]):
        return "R003"
    return None


def _reason_from_speech(spoken: str, recipe_name: str) -> str:
    spoken_short = " ".join(str(spoken).split())
    if len(spoken_short) > 40:
        spoken_short = spoken_short[:40].rstrip() + "…"
    if spoken_short:
        return f"{spoken_short}라고 하셔서 {recipe_name} 향이 좋을 것 같아요."
    return f"{recipe_name} 향이 지금 맞을 것 같아요."


def prefer_speech_recipe(payload: dict, result: dict) -> dict:
    global _last_recipe_id
    spoken = _user_spoken(payload)
    speech_id = infer_recipe_from_speech(spoken)
    if not speech_id or speech_id == result.get("recipe_id"):
        return result

    recipe = recipe_by_id(speech_id)
    if recipe is None:
        return result

    print(
        f"[llm-bridge] 말 우선 {result.get('recipe_id')} → {speech_id}",
        file=sys.stderr,
    )
    _last_recipe_id = speech_id
    return normalize_recommendation(
        {
            "recipe_id": speech_id,
            "intensity": result.get("intensity", 2),
            "duration_seconds": result.get("duration_seconds", 15),
            "reason": _reason_from_speech(spoken, recipe["displayName"]),
            "source": result.get("source", "nim"),
        }
    )


def mock_recommendation(payload: dict) -> dict:
    global _last_recipe_id
    spoken = _user_spoken(payload)
    blocked = _blocked_recipe_ids(payload)

    speech_id = infer_recipe_from_speech(spoken)
    if speech_id:
        preferred = speech_id
    else:
        preferred = _default_recipe_by_time(str(payload.get("timePeriod") or ""))
        air_status = str(payload.get("bme688", {}).get("airStatus") or "")
        if any(k in air_status for k in ["나쁨", "오염"]):
            preferred = "R003"

    if speech_id and speech_id not in blocked:
        recipe_id = speech_id
        _last_recipe_id = recipe_id
    else:
        recipe_id = _pick_recipe_id(preferred, blocked)

    intensity = parse_intensity_from_text(spoken)
    recipe = recipe_by_id(recipe_id)
    return {
        "recipe_id": recipe_id,
        "intensity": intensity,
        "duration_seconds": 15,
        "reason": _reason_from_speech(spoken, recipe["displayName"]),
        "source": "mock",
    }


def parse_intensity_from_text(text: str) -> int:
    normalized = str(text or "").replace(" ", "")
    if any(k in normalized for k in ["약", "은은", "가볍", "살짝"]):
        return 1
    if any(k in normalized for k in ["강", "진하게", "많이"]):
        return 3
    return 2


def _english_scent_reason(reason: str) -> str:
    text = str(reason or "")
    return (
        text.replace("플로럴", "Floral")
        .replace("시트러스", "Citrus")
        .replace("우디", "Woody")
    )


def normalize_recommendation(raw: dict) -> dict:
    recipe_id = str(raw.get("recipe_id", "")).strip()
    recipe = recipe_by_id(recipe_id)
    if recipe is None:
        raise ValueError(f"유효하지 않은 recipe_id: {recipe_id}")

    intensity = min(3, max(1, int(raw.get("intensity", 2))))
    duration = min(
        MAX_CONTINUOUS_SPRAY_SECONDS,
        max(5, int(raw.get("duration_seconds", 15))),
    )

    return {
        "recipe_id": recipe_id,
        "recipe_name": recipe["displayName"],
        "intensity": intensity,
        "duration_seconds": duration,
        "reason": _english_scent_reason(
            str(raw.get("reason", "")).strip() or recipe["description"]
        ),
        "source": raw.get("source", "nim"),
    }


def recommend(payload: dict) -> dict:
    if not NVIDIA_API_KEY:
        return normalize_recommendation(mock_recommendation(payload))

    recipes = load_recipes()
    messages = [
        {"role": "system", "content": build_system_prompt(recipes)},
        {
            "role": "user",
            "content": build_user_prompt(payload),
        },
    ]

    try:
        content = call_nim_chat(messages)
        parsed = extract_json(content)
        parsed["source"] = "nim"
        result = prefer_speech_recipe(payload, normalize_recommendation(parsed))
        blocked = _blocked_recipe_ids(payload)
        if result["recipe_id"] in blocked and infer_recipe_from_speech(_user_spoken(payload)) is None:
            fallback = mock_recommendation(payload)
            print(
                f"[llm-bridge] {result['recipe_id']} 차단 → {fallback['recipe_id']}",
                file=sys.stderr,
            )
            return normalize_recommendation(fallback)
        global _last_recipe_id
        _last_recipe_id = result["recipe_id"]
        return result
    except (urllib.error.URLError, TimeoutError, KeyError, ValueError, json.JSONDecodeError) as exc:
        print(f"[llm-bridge] NIM 오류, 폴백 사용: {exc}", file=sys.stderr)
        return normalize_recommendation(mock_recommendation(payload))


def validate_dispense(payload: dict) -> dict:
    recipe_id = payload.get("recipe_id")
    intensity = payload.get("intensity", 2)
    duration = payload.get("duration_seconds", 15)
    cartridge = payload.get("cartridgeRemaining", {})
    last_dispense_at = payload.get("lastDispenseAt")
    context = payload.get("context", {})

    errors: list[str] = []
    warnings: list[str] = []

    recipe = recipe_by_id(str(recipe_id))
    if recipe is None:
        return {
            "valid": False,
            "errors": [f"등록되지 않은 레시피 ID: {recipe_id}"],
            "warnings": [],
            "adjustedDuration": 0,
        }

    level = min(3, max(1, int(intensity)))
    adj_duration = min(MAX_DURATION_SECONDS, max(5, int(duration)))

    if adj_duration > MAX_CONTINUOUS_SPRAY_SECONDS:
        warnings.append(
            f"연속 분사 {adj_duration}초 → {MAX_CONTINUOUS_SPRAY_SECONDS}초로 조정"
        )
        adj_duration = MAX_CONTINUOUS_SPRAY_SECONDS

    if last_dispense_at:
        try:
            last = datetime.fromisoformat(last_dispense_at.replace("Z", "+00:00"))
            elapsed = (datetime.now(timezone.utc) - last.astimezone(timezone.utc)).total_seconds()
            if elapsed < MIN_COOLDOWN_SECONDS:
                errors.append(
                    f"쿨다운 중 ({int(MIN_COOLDOWN_SECONDS - elapsed)}초 남음)"
                )
        except ValueError:
            pass

    if context.get("mqtt", {}).get("isWoody") and recipe_id == "R001":
        errors.append("실내 우디 냄새 감지 — 우디 향 사용 불가")

    bme = context.get("bme688", {})
    if bme.get("humidity") is not None and bme["humidity"] < 30 and recipe_id == "R003":
        errors.append("습도 낮음 — 시트러스 향 사용 불가")

    for channel, share in recipe["blend"].items():
        if share <= 0:
            continue
        remaining = cartridge.get(channel)
        if remaining is not None and remaining < MIN_CARTRIDGE_PERCENT:
            errors.append(f"{channel} 카트리지 부족 ({remaining}%)")

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "adjustedDuration": adj_duration,
        "intensity": level,
        "recipe": recipe,
    }


def blend_to_weights(blend: dict) -> str:
    channels = ["musk", "lavender", "woody"]
    values = [blend.get(ch, 0) for ch in channels]
    total = sum(values) or 1
    raw = [v / total * 9 for v in values]
    weights = [int(v) for v in raw]
    remainder = 9 - sum(weights)
    order = sorted(range(3), key=lambda i: raw[i] - weights[i], reverse=True)
    for i in range(remainder):
        weights[order[i % 3]] += 1
    return "".join(str(w) for w in weights)


def publish_mqtt_command(command: dict) -> bool:
    global mqtt_client, mqtt_connected, mqtt_error

    if mqtt is None:
        mqtt_error = "paho-mqtt 미설치"
        return False

    with mqtt_lock:
        if mqtt_client is None or not mqtt_connected:
            mqtt_error = "MQTT 미연결"
            return False

        payload = json.dumps(command, ensure_ascii=False)
        result = mqtt_client.publish(MQTT_CONTROL_TOPIC, payload, qos=1)
        result.wait_for_publish(timeout=5)
        print(f"[llm-bridge] MQTT → {MQTT_CONTROL_TOPIC}: {payload}", file=sys.stderr)
        return result.rc == mqtt.MQTT_ERR_SUCCESS


def on_mqtt_connect(client, userdata, flags, rc) -> None:
    global mqtt_connected, mqtt_error

    if rc == 0:
        mqtt_connected = True
        mqtt_error = None
        print("[llm-bridge] MQTT 제어 클라이언트 연결됨", file=sys.stderr)
        return

    mqtt_connected = False
    mqtt_error = f"MQTT 연결 실패 (rc={rc})"


_mqtt_retry_log_at = 0.0


def _log_mqtt_retry(message: str) -> None:
    global _mqtt_retry_log_at
    now = time.monotonic()
    if _mqtt_retry_log_at and now - _mqtt_retry_log_at < 30:
        return
    _mqtt_retry_log_at = now
    print(message, file=sys.stderr)


def on_mqtt_connect_fail(client, userdata) -> None:
    global mqtt_connected, mqtt_error
    mqtt_connected = False
    mqtt_error = "MQTT 연결 실패, 재시도 중"
    _log_mqtt_retry(f"[llm-bridge] {mqtt_error}")


def on_mqtt_disconnect(client, userdata, rc) -> None:
    global mqtt_connected, mqtt_error
    mqtt_connected = False
    if rc != 0:
        mqtt_error = f"MQTT 연결 끊김, 재시도 중 (rc={rc})"
        _log_mqtt_retry(f"[llm-bridge] {mqtt_error}")


def start_mqtt_publisher() -> None:
    global mqtt_client, mqtt_error

    if mqtt is None:
        mqtt_error = "paho-mqtt 미설치"
        return
    if not MQTT_BROKER:
        mqtt_error = "MQTT_BROKER 환경 변수가 설정되지 않았습니다 (.env.local 확인)"
        print(f"[llm-bridge] {mqtt_error}", file=sys.stderr)
        return

    client = mqtt.Client(client_id="Dashboard_LLM_Control", protocol=mqtt.MQTTv311)
    client.username_pw_set(MQTT_USER, MQTT_PASS)
    client.tls_set(cert_reqs=ssl.CERT_NONE)
    client.tls_insecure_set(True)
    client.on_connect = on_mqtt_connect
    client.on_connect_fail = on_mqtt_connect_fail
    client.on_disconnect = on_mqtt_disconnect
    client.reconnect_delay_set(min_delay=2, max_delay=30)
    client.connect_async(MQTT_BROKER, MQTT_PORT, keepalive=60)
    mqtt_client = client
    client.loop_start()


class LlmBridgeHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw.decode("utf-8"))

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
        if self.path == "/api/llm/health":
            self._send_json(
                200,
                {
                    "ok": True,
                    "nimConfigured": bool(NVIDIA_API_KEY),
                    "model": NIM_MODEL,
                    "mqttConnected": mqtt_connected,
                    "mqttTopic": MQTT_CONTROL_TOPIC,
                    "mqttError": mqtt_error,
                },
            )
            return

        if self.path == "/api/llm/recipes":
            self._send_json(200, {"ok": True, "recipes": load_recipes()})
            return

        self._send_json(404, {"ok": False})

    def do_POST(self) -> None:  # noqa: N802
        try:
            body = self._read_json()
        except json.JSONDecodeError as exc:
            self._send_json(400, {"ok": False, "error": str(exc)})
            return

        if self.path == "/api/llm/recommend":
            try:
                result = recommend(body)
                self._send_json(200, {"ok": True, **result})
            except ValueError as exc:
                self._send_json(422, {"ok": False, "error": str(exc)})
            except Exception as exc:  # noqa: BLE001
                self._send_json(500, {"ok": False, "error": str(exc)})
            return

        if self.path == "/api/llm/validate":
            result = validate_dispense(body)
            self._send_json(200, {"ok": True, **result})
            return

        if self.path == "/api/llm/dispense":
            validation = validate_dispense(body)
            if not validation["valid"]:
                self._send_json(
                    422,
                    {"ok": False, "validation": validation},
                )
                return

            recipe = validation["recipe"]
            intensity = validation["intensity"]
            duration = validation["adjustedDuration"]
            weights = blend_to_weights(recipe["blend"])

            mqtt_command = {
                "action": "dispense",
                "recipe_id": recipe["id"],
                "recipe_name": recipe["displayName"],
                "level": intensity,
                "weights": weights,
                "duration_seconds": duration,
                "commands": [f"LVL{intensity}", f"ON{weights}"],
                "issued_at": datetime.now(timezone.utc).isoformat(),
            }

            sent = publish_mqtt_command(mqtt_command)
            self._send_json(
                200,
                {
                    "ok": True,
                    "validation": validation,
                    "mqtt": {"sent": sent, "topic": MQTT_CONTROL_TOPIC},
                    "command": mqtt_command,
                },
            )
            return

        self._send_json(404, {"ok": False})


def main() -> None:
    start_mqtt_publisher()

    server = ThreadingHTTPServer(("127.0.0.1", PORT), LlmBridgeHandler)
    print(
        f"[llm-bridge] http://127.0.0.1:{PORT} (NIM: {NIM_MODEL}, key={'set' if NVIDIA_API_KEY else 'mock'})",
        file=sys.stderr,
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
