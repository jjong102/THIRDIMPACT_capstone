#!/usr/bin/env python3
"""향기 추천 AI 대사 → 한국어 TTS → 스피커 재생 HTTP 브리지."""

from __future__ import annotations

import asyncio
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORT = int(os.environ.get("TTS_BRIDGE_PORT", "5180"))
TTS_VOICE = os.environ.get("TTS_VOICE", "ko-KR-SunHiNeural")
TTS_RATE = os.environ.get("TTS_RATE", "-8%")
TTS_LANGUAGE = os.environ.get("TTS_LANGUAGE", "ko")
TTS_PLAY_DEVICE = os.environ.get("TTS_PLAY_DEVICE", "UACDemo")
TTS_MIC_DEVICE = os.environ.get("TTS_MIC_DEVICE", "MUSIC-BOOST")
DEFAULT_VOLUME = int(os.environ.get("TTS_VOLUME", "80"))
VOLUME_PATH = Path(
    os.environ.get(
        "TTS_VOLUME_PATH",
        Path(__file__).resolve().parent / ".speaker-volume.json",
    )
)

_generation = 0
_generation_lock = threading.Lock()
_play_proc: subprocess.Popen | None = None
_play_proc_lock = threading.Lock()
_engine_error: str | None = None
_job_queue: queue.Queue[tuple[int, str, str]] = queue.Queue()
VOICE_CLIP_DIR = Path(__file__).resolve().parent.parent / "public" / "voice-clips"
VOICE_CLIP_EXT = {".wav", ".mp3", ".ogg", ".m4a"}
_ODOR_LINES = (
    "거실에서 악취를 감지했습니다.",
)
_recent_spoken_at: dict[str, float] = {}
_recent_spoken_lock = threading.Lock()
TTS_DEDUP_SECONDS = float(os.environ.get("TTS_DEDUP_SECONDS", "15"))
ODOR_DEDUP_SECONDS = 60.0


def _which(*names: str) -> str | None:
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return None


def _pulse_sinks() -> list[str]:
    pactl = _which("pactl")
    if not pactl:
        return []
    result = subprocess.run(
        [pactl, "list", "short", "sinks"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        check=False,
    )
    sinks: list[str] = []
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            sinks.append(parts[1])
    return sinks


def resolve_preferred_sink() -> str | None:
    match = (TTS_PLAY_DEVICE or "UACDemo").strip()
    if not match:
        return None
    sinks = _pulse_sinks()
    lowered = match.lower()
    for sink in sinks:
        if sink == match or lowered in sink.lower():
            return sink
    return None


def apply_preferred_sink() -> str | None:
    sink = resolve_preferred_sink()
    if not sink:
        return None
    os.environ["PULSE_SINK"] = sink
    pactl = _which("pactl")
    if pactl:
        subprocess.run(
            [pactl, "set-default-sink", sink],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    return sink


def _pulse_sources() -> list[str]:
    pactl = _which("pactl")
    if not pactl:
        return []
    result = subprocess.run(
        [pactl, "list", "short", "sources"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        check=False,
    )
    sources: list[str] = []
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        name = parts[1]
        if name.endswith(".monitor"):
            continue
        sources.append(name)
    return sources


def resolve_preferred_source() -> str | None:
    match = (TTS_MIC_DEVICE or "MUSIC-BOOST").strip()
    sources = _pulse_sources()
    if match:
        lowered = match.lower()
        for source in sources:
            if source == match or lowered in source.lower():
                return source
    return sources[0] if sources else None


def apply_preferred_source() -> str | None:
    source = resolve_preferred_source()
    if not source:
        return None
    os.environ["PULSE_SOURCE"] = source
    pactl = _which("pactl")
    if pactl:
        subprocess.run(
            [pactl, "set-default-source", source],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        subprocess.run(
            [pactl, "set-source-mute", source, "0"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    return source


def clamp_volume(value) -> int:
    try:
        return max(0, min(100, int(round(float(value)))))
    except (TypeError, ValueError):
        return DEFAULT_VOLUME


def load_saved_volume() -> int:
    try:
        payload = json.loads(VOLUME_PATH.read_text(encoding="utf-8"))
        return clamp_volume(payload.get("volume", DEFAULT_VOLUME))
    except Exception:
        return DEFAULT_VOLUME


def save_volume(volume: int) -> int:
    next_volume = clamp_volume(volume)
    VOLUME_PATH.write_text(
        json.dumps({"volume": next_volume}, ensure_ascii=False),
        encoding="utf-8",
    )
    return next_volume


def read_sink_volume(sink: str | None) -> int | None:
    pactl = _which("pactl")
    if not pactl or not sink:
        return None
    result = subprocess.run(
        [pactl, "get-sink-volume", sink],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        check=False,
    )
    match = re.search(r"(\d+)%", result.stdout or "")
    if not match:
        return None
    return clamp_volume(match.group(1))


def apply_sink_volume(volume: int | None = None, sink: str | None = None) -> dict:
    next_volume = clamp_volume(volume if volume is not None else load_saved_volume())
    target = sink or apply_preferred_sink()
    pactl = _which("pactl")
    if pactl and target:
        subprocess.run(
            [pactl, "set-sink-mute", target, "0"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        subprocess.run(
            [pactl, "set-sink-volume", target, f"{next_volume}%"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    return {
        "volume": read_sink_volume(target) or next_volume,
        "sink": target,
    }


def volume_status() -> dict:
    sink = apply_preferred_sink()
    saved = load_saved_volume()
    return {
        "volume": read_sink_volume(sink) or saved,
        "saved": saved,
        "sink": sink,
    }


def _player_env() -> dict[str, str]:
    env = os.environ.copy()
    sink = apply_preferred_sink()
    if sink:
        env["PULSE_SINK"] = sink
        apply_sink_volume(load_saved_volume(), sink)
    return env


def _has_edge_tts() -> bool:
    try:
        import edge_tts  # noqa: F401

        return True
    except Exception:
        return False


def detect_engine() -> str:
    if _has_edge_tts():
        return "edge-tts"
    if _which("espeak-ng", "espeak"):
        return "espeak"
    return "none"


def normalize_speech_text(text: str) -> str:
    cleaned = str(text or "").strip()
    cleaned = cleaned.replace("· ", "")
    cleaned = cleaned.replace("—", ", ")
    cleaned = cleaned.replace("–", ", ")
    cleaned = re.sub(r"LVL\s*(\d)", r"레벨 \1", cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.replace("%", "퍼센트")
    cleaned = re.sub(r"\n+", ". ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def bump_generation() -> int:
    global _generation
    with _generation_lock:
        _generation += 1
        return _generation


def current_generation() -> int:
    with _generation_lock:
        return _generation


def stop_playback() -> None:
    global _play_proc
    bump_generation()
    while True:
        try:
            _job_queue.get_nowait()
        except queue.Empty:
            break
    with _play_proc_lock:
        proc = _play_proc
        _play_proc = None
    if proc and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=1.2)
        except subprocess.TimeoutExpired:
            proc.kill()


def _run_player(command: list[str], gen: int) -> None:
    global _play_proc
    if gen != current_generation():
        return

    with _play_proc_lock:
        proc = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=_player_env(),
        )
        _play_proc = proc

    proc.wait()

    with _play_proc_lock:
        if _play_proc is proc:
            _play_proc = None


def play_audio_file(path: str, gen: int) -> None:
    ffplay = _which("ffplay")
    if ffplay:
        _run_player(
            [ffplay, "-nodisp", "-autoexit", "-loglevel", "quiet", path],
            gen,
        )
        return

    mpv = _which("mpv")
    if mpv:
        _run_player([mpv, "--no-video", "--really-quiet", path], gen)
        return

    gstplay = _which("gst-play-1.0")
    if gstplay:
        _run_player([gstplay, "-q", path], gen)
        return

    mpg123 = _which("mpg123")
    if mpg123 and path.endswith(".mp3"):
        _run_player([mpg123, "-q", path], gen)
        return

    wav_path = path
    tmp_wav: str | None = None
    if not path.endswith(".wav"):
        ffmpeg = _which("ffmpeg")
        if not ffmpeg:
            raise RuntimeError("mp3를 재생할 ffplay/mpv/ffmpeg가 없습니다")
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp.close()
        tmp_wav = tmp.name
        converted = subprocess.run(
            [ffmpeg, "-y", "-i", path, "-ar", "22050", "-ac", "1", tmp_wav],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if converted.returncode != 0:
            Path(tmp_wav).unlink(missing_ok=True)
            raise RuntimeError("오디오 변환에 실패했습니다")
        wav_path = tmp_wav

    try:
        paplay = _which("paplay")
        aplay = _which("aplay")
        if paplay:
            command = [paplay]
            sink = resolve_preferred_sink()
            if sink:
                command.extend(["--device", sink])
            command.append(wav_path)
        elif aplay:
            command = [aplay, "-q", "-D", "plughw:CARD=UACDemoV10,DEV=0", wav_path]
        else:
            raise RuntimeError("스피커 재생 프로그램(paplay/aplay)을 찾을 수 없습니다")
        _run_player(command, gen)
    finally:
        if tmp_wav:
            Path(tmp_wav).unlink(missing_ok=True)


def synthesize_edge(text: str, dest: str) -> None:
    import edge_tts

    async def _save() -> None:
        communicate = edge_tts.Communicate(text, TTS_VOICE, rate=TTS_RATE)
        await communicate.save(dest)

    asyncio.run(_save())


def speak_espeak(text: str, gen: int) -> None:
    binary = _which("espeak-ng", "espeak")
    if not binary:
        raise RuntimeError("espeak-ng가 설치되어 있지 않습니다")

    _run_player([binary, "-v", TTS_LANGUAGE, "-s", "145", "-a", "160", text], gen)


def speak_now(text: str, gen: int) -> str:
    engine = detect_engine()
    if engine == "none":
        raise RuntimeError(
            "TTS 엔진이 없습니다. python3 -m pip install edge-tts 후 다시 시도하세요."
        )

    if engine == "espeak":
        speak_espeak(text, gen)
        return engine

    tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
    tmp.close()
    audio_path = tmp.name
    try:
        synthesize_edge(text, audio_path)
        if gen != current_generation():
            return engine
        play_audio_file(audio_path, gen)
        return engine
    finally:
        Path(audio_path).unlink(missing_ok=True)


def enqueue_speak(text: str) -> dict:
    spoken = normalize_speech_text(text)
    if not spoken:
        return {"ok": True, "skipped": True, "reason": "empty"}

    window = ODOR_DEDUP_SECONDS if spoken in _ODOR_LINES else TTS_DEDUP_SECONDS
    now = time.time()
    with _recent_spoken_lock:
        last = _recent_spoken_at.get(spoken, 0.0)
        if now - last < window:
            return {"ok": True, "skipped": True, "reason": "duplicate"}
        _recent_spoken_at[spoken] = now

    gen = current_generation()
    _job_queue.put((gen, "speak", spoken))
    return {
        "ok": True,
        "queued": True,
        "engine": detect_engine(),
        "voice": TTS_VOICE,
        "text": spoken,
    }


def list_voice_clips() -> list[dict]:
    root = VOICE_CLIP_DIR.resolve()
    root.mkdir(parents=True, exist_ok=True)
    clips = []
    for path in sorted(root.iterdir(), key=lambda item: item.name.lower()):
        if not path.is_file() or path.name.startswith("."):
            continue
        if path.suffix.lower() not in VOICE_CLIP_EXT:
            continue
        clips.append({"file": path.name, "label": path.stem})
    return clips


def resolve_voice_clip(filename: str) -> Path:
    name = Path(str(filename or "")).name
    suffix = Path(name).suffix.lower()
    if suffix not in VOICE_CLIP_EXT:
        raise ValueError("wav, mp3, ogg, m4a 파일만 재생할 수 있습니다")

    root = VOICE_CLIP_DIR.resolve()
    path = (root / name).resolve()
    if path.parent != root:
        raise ValueError("잘못된 파일입니다")
    if not path.is_file():
        raise FileNotFoundError(
            f"{name} 파일이 없습니다. public/voice-clips 폴더에 넣어 주세요."
        )
    return path


def enqueue_clip(filename: str) -> dict:
    path = resolve_voice_clip(filename)
    stop_playback()
    gen = current_generation()
    _job_queue.put((gen, "clip", str(path)))
    return {"ok": True, "queued": True, "file": path.name}


def _speak_worker() -> None:
    global _engine_error

    while True:
        gen, kind, payload = _job_queue.get()
        if gen != current_generation():
            continue
        try:
            if kind == "clip":
                play_audio_file(payload, gen)
            else:
                speak_now(payload, gen)
        except Exception as exc:  # noqa: BLE001
            _engine_error = str(exc)
            print(f"[tts-bridge] {exc}", file=sys.stderr)


class TtsBridgeHandler(BaseHTTPRequestHandler):
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
        path = self.path.split("?", 1)[0].rstrip("/")
        if path == "/api/tts/clips":
            self._send_json(200, {"ok": True, "clips": list_voice_clips()})
            return

        if path == "/api/tts/volume":
            self._send_json(200, {"ok": True, **volume_status()})
            return

        if path != "/api/tts/health":
            self._send_json(404, {"ok": False})
            return

        engine = detect_engine()
        self._send_json(
            200,
            {
                "ok": engine != "none",
                "engine": engine,
                "voice": TTS_VOICE,
                "language": TTS_LANGUAGE,
                "player": _which("ffplay", "mpv", "gst-play-1.0", "mpg123", "paplay", "aplay"),
                "sink": resolve_preferred_sink(),
                "volume": volume_status().get("volume"),
                "engineError": _engine_error,
            },
        )

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0].rstrip("/")
        if path == "/api/tts/stop":
            stop_playback()
            self._send_json(200, {"ok": True, "stopped": True})
            return

        if path == "/api/tts/volume":
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(raw.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                self._send_json(400, {"ok": False, "error": "잘못된 JSON입니다"})
                return
            volume = save_volume(payload.get("volume", DEFAULT_VOLUME))
            result = apply_sink_volume(volume)
            self._send_json(200, {"ok": True, **result})
            return

        if path == "/api/tts/clip":
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(raw.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                self._send_json(400, {"ok": False, "error": "잘못된 JSON입니다"})
                return

            try:
                result = enqueue_clip(str(payload.get("file") or ""))
            except FileNotFoundError as exc:
                self._send_json(404, {"ok": False, "missing": True, "error": str(exc)})
                return
            except ValueError as exc:
                self._send_json(400, {"ok": False, "error": str(exc)})
                return
            except Exception as exc:  # noqa: BLE001
                print(f"[tts-bridge] {exc}", file=sys.stderr)
                self._send_json(500, {"ok": False, "error": str(exc)})
                return

            print(f"[tts-bridge] clip → {result.get('file')}", file=sys.stderr)
            self._send_json(200, result)
            return

        if path != "/api/tts/speak":
            self._send_json(404, {"ok": False})
            return

        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"

        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            self._send_json(400, {"ok": False, "error": "잘못된 JSON입니다"})
            return

        text = str(payload.get("text") or "").strip()
        if not text:
            self._send_json(400, {"ok": False, "error": "읽을 문장이 없습니다"})
            return

        if len(text) > 1200:
            text = text[:1200]

        try:
            result = enqueue_speak(text)
            print(f"[tts-bridge] → {result.get('text', text)[:80]}", file=sys.stderr)
            self._send_json(200, result)
        except Exception as exc:  # noqa: BLE001
            global _engine_error
            _engine_error = str(exc)
            print(f"[tts-bridge] {exc}", file=sys.stderr)
            self._send_json(500, {"ok": False, "error": str(exc)})


def main() -> None:
    engine = detect_engine()
    sink = apply_preferred_sink()
    source = apply_preferred_source()
    if not VOLUME_PATH.exists():
        save_volume(DEFAULT_VOLUME)
    volume = apply_sink_volume(load_saved_volume(), sink)
    worker = threading.Thread(target=_speak_worker, name="tts-worker", daemon=True)
    worker.start()
    server = ThreadingHTTPServer(("127.0.0.1", PORT), TtsBridgeHandler)
    print(
        f"[tts-bridge] http://127.0.0.1:{PORT} (engine={engine}, voice={TTS_VOICE}, sink={sink or 'default'}, source={source or 'default'}, volume={volume.get('volume')}%)",
        file=sys.stderr,
    )
    if engine == "none":
        print(
            "[tts-bridge] edge-tts 또는 espeak-ng가 필요합니다: python3 -m pip install --user edge-tts",
            file=sys.stderr,
        )
    server.serve_forever()


if __name__ == "__main__":
    main()
