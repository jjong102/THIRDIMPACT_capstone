#!/usr/bin/env python3
"""Arduino 향기 컨트롤러 시리얼 브리지 (HTTP → USB serial)."""

from __future__ import annotations

import glob
import json
import os
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

try:
    import serial
except ImportError:  # pragma: no cover - optional on dev machines
    serial = None  # type: ignore

PORT = int(os.environ.get("FRAGRANCE_BRIDGE_PORT", "5174"))
SERIAL_PORT = os.environ.get("ARDUINO_SERIAL_PORT", "").strip()
BAUD_RATE = int(os.environ.get("ARDUINO_BAUD_RATE", "9600"))
# Uno는 포트 오픈 시 DTR로 리셋되고 부트로더가 ~2초 대기한다.
READY_WAIT_S = float(os.environ.get("ARDUINO_READY_WAIT_S", "2.2"))
RECONNECT_S = float(os.environ.get("ARDUINO_RECONNECT_S", "3"))
USB_RESET_S = float(os.environ.get("ARDUINO_USB_RESET_S", "20"))
STATE_PATH = Path(
    os.environ.get(
        "FRAGRANCE_STATE_PATH",
        Path(__file__).resolve().parent / ".device-state.json",
    )
)
# Arduino / CH340 계열. 포트가 사라져도 USB는 남아 있을 때 소프트 리셋한다.
_ARDUINO_VENDORS = {"2341", "2a03"}
_SERIAL_USB_IDS = {
    ("1a86", "7523"),
    ("1a86", "5523"),
    ("1a86", "7522"),
    ("10c4", "ea60"),
}

ser = None
serial_error = None
serial_lock = threading.Lock()
state_lock = threading.Lock()
_last_usb_reset = 0.0

MODE_RE = re.compile(r"FAN(?: ON)? / MODE\s*(\d)|FAN MODE\s*(\d)")

# 미스트 릴레이 극성. 릴레이 모듈이 LOW=ON 이라 POL0 이 맞다.
# main_opt 펌웨어는 부팅 기본값이 POL1(HIGH=ON)이라, 그대로 두면 "끄기"가 LOW 로 나가
# 가만히 있어도 향이 계속 나온다. 값은 RAM 에만 남으므로 연결·재부팅 때마다 다시 보낸다.
# 빈 문자열이면 보내지 않는다.
MIST_POLARITY_CMD = os.environ.get("ARDUINO_MIST_POLARITY", "POL0").strip().upper()
# setup() 이 찍는 문구. 명령 응답에 섞여 오면 아두이노가 리셋된 것이다.
REBOOT_RE = re.compile(r"READY|M133 /")


def _looks_rebooted(replies: list[str]) -> bool:
    return any(REBOOT_RE.search(str(line)) for line in replies)


def list_serial_candidates() -> list[str]:
    preferred: list[str] = []
    env_port = os.environ.get("ARDUINO_SERIAL_PORT", "").strip()
    if env_port:
        preferred.append(env_port)
    preferred.extend(sorted(glob.glob("/dev/ttyACM*")))
    preferred.extend(sorted(glob.glob("/dev/ttyUSB*")))
    seen: set[str] = set()
    out: list[str] = []
    for path in preferred:
        if path in seen:
            continue
        seen.add(path)
        out.append(path)
    return out


def _serial_handle_valid(port) -> bool:
    if port is None:
        return False
    try:
        device = str(getattr(port, "port", "") or "")
        return bool(port.is_open and device and Path(device).exists())
    except Exception:
        return False


def default_state() -> dict:
    return {
        "rev": 0,
        "airPurifierOn": False,
        "airPurifierMode": 1,
        "fragranceOn": False,
        "fragranceChannels": {
            "musk": False,
            "lavender": False,
            "woody": False,
        },
        "fragranceLevel": 2,
        "fragranceBlend": {
            "musk": 40,
            "lavender": 35,
            "woody": 25,
        },
        "fragranceDiffusing": False,
        "updatedAt": None,
    }


def _load_state() -> dict:
    state = default_state()
    try:
        raw = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            state.update({key: raw[key] for key in state if key in raw})
    except (OSError, json.JSONDecodeError, TypeError):
        pass
    return state


def _save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATE_PATH)


device_state = _load_state()


def snapshot_state() -> dict:
    with state_lock:
        return json.loads(json.dumps(device_state))


def merge_state(patch: dict | None) -> dict:
    if not isinstance(patch, dict):
        return snapshot_state()

    with state_lock:
        for key, value in patch.items():
            if key in ("rev", "updatedAt"):
                continue
            if key not in device_state:
                continue
            if key == "airPurifierMode":
                mode = int(value) if str(value).isdigit() else 1
                device_state[key] = mode if 1 <= mode <= 3 else 1
            elif key == "fragranceLevel":
                level = int(value) if str(value).isdigit() else 2
                device_state[key] = level if 1 <= level <= 3 else 2
            elif key in ("fragranceChannels", "fragranceBlend") and isinstance(value, dict):
                current = dict(device_state[key])
                current.update(value)
                device_state[key] = current
            elif key in ("airPurifierOn", "fragranceOn", "fragranceDiffusing"):
                device_state[key] = bool(value)
            else:
                device_state[key] = value

        device_state["rev"] = int(device_state.get("rev") or 0) + 1
        device_state["updatedAt"] = time.time()
        _save_state(device_state)
        return json.loads(json.dumps(device_state))


def apply_replies_to_state(replies: list[str]) -> dict:
    patch: dict = {}
    for line in replies:
        text = str(line)
        if "FAN OFF" in text and "이미" not in text:
            patch["airPurifierOn"] = False
            continue

        match = MODE_RE.search(text)
        if match:
            mode = int(match.group(1) or match.group(2))
            if 1 <= mode <= 3:
                patch["airPurifierOn"] = True
                patch["airPurifierMode"] = mode
            continue

        if "FAN 이미 ON" in text:
            patch["airPurifierOn"] = True

    if patch:
        return merge_state(patch)
    return snapshot_state()


def _drain(port) -> None:
    try:
        waiting = getattr(port, "in_waiting", 0) or 0
        if waiting:
            port.read(waiting)
        port.reset_input_buffer()
    except Exception:
        pass


def _wait_until_ready(port) -> None:
    deadline = time.monotonic() + READY_WAIT_S
    while time.monotonic() < deadline:
        line = port.readline()
        if line and b"READY" in line:
            break
    _drain(port)


def _read_sys(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip().lower()
    except OSError:
        return ""


def _write_sys(path: Path, value: str) -> bool:
    try:
        path.write_text(value, encoding="utf-8")
        return True
    except OSError:
        return False


def _list_usb_serial_devices() -> list[Path]:
    base = Path("/sys/bus/usb/devices")
    if not base.exists():
        return []

    found: list[Path] = []
    for vendor_file in base.glob("*/idVendor"):
        dev = vendor_file.parent
        vendor = _read_sys(vendor_file)
        product = _read_sys(dev / "idProduct")
        if vendor in _ARDUINO_VENDORS or (vendor, product) in _SERIAL_USB_IDS:
            found.append(dev)
    return found


def _usb_keep_awake(dev: Path) -> None:
    control = dev / "power" / "control"
    if control.exists():
        _write_sys(control, "on")
    autosuspend = dev / "power" / "autosuspend"
    if autosuspend.exists():
        _write_sys(autosuspend, "-1")


def _try_usb_soft_reset() -> bool:
    """포트는 없는데 USB 장치는 남아 있으면 다시 꽂지 않고 깨운다."""
    global _last_usb_reset, serial_error

    if any(Path(path).exists() for path in list_serial_candidates()):
        return False

    devices = _list_usb_serial_devices()
    if not devices:
        return False

    now = time.monotonic()
    if now - _last_usb_reset < USB_RESET_S:
        return False
    _last_usb_reset = now

    reset_any = False
    for dev in devices:
        _usb_keep_awake(dev)
        authorized = dev / "authorized"
        if not authorized.exists():
            continue
        print(f"[fragrance-bridge] USB 소프트 리셋 {dev.name}", file=sys.stderr)
        if _write_sys(authorized, "0"):
            time.sleep(0.4)
            reset_any = _write_sys(authorized, "1") or reset_any

    if reset_any:
        serial_error = "Arduino USB 재인식 중"
        time.sleep(1.2)
    return reset_any


def _open_serial_candidate(path: str):
    if serial is None:
        raise RuntimeError("pyserial 미설치")

    candidate = serial.Serial()
    candidate.port = path
    candidate.baudrate = BAUD_RATE
    candidate.timeout = 0.3
    candidate.write_timeout = 1
    # 재연결마다 DTR로 Uno를 리셋하지 않는다. USB 인식 때 이미 한 번 리셋된다.
    try:
        candidate.dtr = False
        candidate.rts = False
    except Exception:
        pass
    candidate.open()
    return candidate


def _open_serial_locked() -> bool:
    global ser, serial_error, SERIAL_PORT

    if serial is None:
        serial_error = "pyserial 미설치 (pip install pyserial)"
        return False

    if _serial_handle_valid(ser):
        return True

    if ser is not None:
        try:
            ser.close()
        except Exception:
            pass
        ser = None

    last_error = "Arduino 시리얼 포트 없음 (/dev/ttyACM* · /dev/ttyUSB*)"
    for path in list_serial_candidates():
        if not Path(path).exists():
            continue
        candidate = None
        try:
            candidate = _open_serial_candidate(path)
            _wait_until_ready(candidate)
            _apply_polarity(candidate)
            ser = candidate
            SERIAL_PORT = path
            serial_error = None
            print(f"[fragrance-bridge] serial {path} @ {BAUD_RATE}", file=sys.stderr)
            return True
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
            if candidate is not None:
                try:
                    candidate.close()
                except Exception:
                    pass
            ser = None

    serial_error = last_error
    return False


def _reconnect_loop() -> None:
    while True:
        time.sleep(RECONNECT_S)
        try:
            if _serial_handle_valid(ser):
                continue
            if not open_serial():
                _try_usb_soft_reset()
        except Exception as exc:  # noqa: BLE001
            print(f"[fragrance-bridge] 재연결 실패: {exc}", file=sys.stderr)


def open_serial() -> bool:
    with serial_lock:
        return _open_serial_locked()


def force_reopen_serial() -> bool:
    global ser
    with serial_lock:
        if ser is not None:
            try:
                ser.close()
            except Exception:
                pass
            ser = None
        if _open_serial_locked():
            return True
    _try_usb_soft_reset()
    with serial_lock:
        return _open_serial_locked()


def _read_replies(port, wait_s: float = 1.0) -> list[str]:
    chunks: list[bytes] = []
    time.sleep(0.05)
    deadline = time.monotonic() + wait_s
    last_data = time.monotonic()
    allow_early_exit = wait_s <= 1.2

    while time.monotonic() < deadline:
        try:
            waiting = getattr(port, "in_waiting", 0) or 0
            if waiting:
                chunks.append(port.read(waiting))
                last_data = time.monotonic()
            else:
                raw = port.read(1)
                if raw:
                    chunks.append(raw)
                    last_data = time.monotonic()
                elif chunks and allow_early_exit and time.monotonic() - last_data > 0.15:
                    break
                else:
                    time.sleep(0.02)
        except Exception:
            break

    text = b"".join(chunks).decode("utf-8", errors="replace")
    replies = [line.strip() for line in text.splitlines() if line.strip()]
    for reply in replies:
        print(f"[fragrance-bridge] ← {reply}", file=sys.stderr)
    return replies


def _write_line(port, line: str) -> list[str]:
    text = line.strip()
    port.write(f"{text}\n".encode("utf-8"))
    port.flush()
    print(f"[fragrance-bridge] → {text}", file=sys.stderr)
    return _read_replies(port, 3.6 if text.upper().startswith("OFF") else 1.0)


def _apply_polarity(port) -> None:
    if not MIST_POLARITY_CMD:
        return
    try:
        _write_line(port, MIST_POLARITY_CMD)
    except Exception as exc:  # noqa: BLE001
        print(f"[fragrance-bridge] 극성 설정 실패: {exc}", file=sys.stderr)


def send_commands(commands: list[str]) -> tuple[bool, list[str]]:
    global ser, serial_error

    with serial_lock:
        if not _open_serial_locked():
            print("[fragrance-bridge mock]", commands, file=sys.stderr)
            return False, ["NOT CONNECTED"]

        assert ser is not None
        replies: list[str] = []
        try:
            ser.timeout = 0.3
            _drain(ser)
            for line in commands:
                line_replies = _write_line(ser, line)
                replies.extend(line_replies)
                if _looks_rebooted(line_replies):
                    # 리셋되면 극성이 POL1 로 돌아가고 방금 명령도 사라진다.
                    print("[fragrance-bridge] 아두이노 재부팅 감지 → 극성 복구 후 재전송", file=sys.stderr)
                    _apply_polarity(ser)
                    replies.extend(_write_line(ser, line))
        except Exception as exc:  # noqa: BLE001
            print(f"[fragrance-bridge] write failed: {exc}", file=sys.stderr)
            try:
                ser.close()
            except Exception:
                pass
            ser = None
            serial_error = str(exc)
            return False, [str(exc)]

    return True, replies


class FragranceBridgeHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0].rstrip("/")

        if path == "/api/fragrance/health":
            connected = open_serial()
            self._send_json(
                200,
                {
                    "ok": True,
                    "connected": connected,
                    "port": SERIAL_PORT,
                    "error": serial_error,
                    "state": snapshot_state(),
                },
            )
            return

        if path == "/api/fragrance/state":
            self._send_json(200, {"ok": True, "state": snapshot_state()})
            return

        self._send_json(404, {"ok": False})

    def do_PUT(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0].rstrip("/")
        if path != "/api/fragrance/state":
            self._send_json(404, {"ok": False})
            return

        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"

        try:
            body = json.loads(raw.decode("utf-8"))
            if not isinstance(body, dict):
                raise ValueError("state must be an object")
        except (json.JSONDecodeError, ValueError) as exc:
            self._send_json(400, {"ok": False, "error": str(exc)})
            return

        patch = body.get("state", body)
        self._send_json(200, {"ok": True, "state": merge_state(patch)})

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0].rstrip("/")
        if path == "/api/fragrance/reconnect":
            connected = force_reopen_serial()
            self._send_json(
                200,
                {
                    "ok": True,
                    "connected": connected,
                    "port": SERIAL_PORT,
                    "error": serial_error,
                    "state": snapshot_state(),
                },
            )
            return

        if path != "/api/fragrance/command":
            self._send_json(404, {"ok": False})
            return

        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"

        try:
            body = json.loads(raw.decode("utf-8"))
            commands = body.get("commands", [])
            if not isinstance(commands, list):
                raise ValueError("commands must be a list")
        except (json.JSONDecodeError, ValueError) as exc:
            self._send_json(400, {"ok": False, "error": str(exc)})
            return

        if isinstance(body.get("state"), dict):
            merge_state(body["state"])

        sent, replies = send_commands([str(cmd) for cmd in commands])
        state = apply_replies_to_state(replies)
        self._send_json(
            200,
            {
                "ok": True,
                "sent": sent,
                "connected": sent,
                "commands": commands,
                "replies": replies,
                "state": state,
            },
        )


def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", PORT), FragranceBridgeHandler)
    threading.Thread(target=_reconnect_loop, name="arduino-reconnect", daemon=True).start()
    print(
        f"[fragrance-bridge] http://127.0.0.1:{PORT} → "
        f"{SERIAL_PORT or 'auto:/dev/ttyACM*|/dev/ttyUSB*'} @ {BAUD_RATE}",
        file=sys.stderr,
    )
    open_serial()
    server.serve_forever()


if __name__ == "__main__":
    main()
