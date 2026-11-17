#!/usr/bin/env python3
"""주행 명령을 주행 젯슨에 보낼 때 스피커로 읽을 문장."""

from __future__ import annotations

import json
import os
import sys
import threading
import urllib.error
import urllib.request

TTS_URL = os.environ.get("TTS_SPEAK_URL", "http://127.0.0.1:5180/api/tts/speak")

SCENT_KO = {
    "mint": "페퍼민트",
    "peppermint": "페퍼민트",
    "lavender": "라벤더",
    "orange": "오렌지",
}

SPRAY_DONE = "발향이 완료되었습니다. 정찰을 시작합니다."
SPRAY_START = "발향을 시작합니다."
PURIFY_AND_SPRAY_START = "공기청정 및 발향을 시작합니다."
ODOR_DEPART = "룸에서 악취가 탐지되었습니다. 룸으로 가서 공기청정 및 발향을 시작합니다."


def scent_ko(name: str | None) -> str:
    key = str(name or "").strip().lower()
    return SCENT_KO.get(key, str(name or "").strip())


def with_ro(name: str) -> str:
    """페퍼민트로 / 라벤더로. 받침이 있으면 으로."""
    value = str(name or "").strip()
    if not value:
        return "선택한 향으로"
    last = ord(value[-1])
    if last < 0xAC00 or last > 0xD7A3:
        return f"{value}으로"
    jong = (last - 0xAC00) % 28
    if jong == 0 or jong == 8:
        return f"{value}로"
    return f"{value}으로"


def scent_configured(name: str | None) -> str:
    return f"{with_ro(scent_ko(name))} 향기가 설정되었습니다. 룸에 가서 발향을 시작합니다."


def scent_weak(name: str | None) -> str:
    label = scent_ko(name) or "향기"
    return f"룸에 {label} 향기가 미약합니다. 다시 룸으로 돌아가서 발향을 시작합니다."


def speak(text: str) -> None:
    spoken = str(text or "").strip()
    if not spoken:
        return

    def _send() -> None:
        _post_speak(spoken, wait=False, timeout=3)

    threading.Thread(target=_send, name="tts-speak", daemon=True).start()


def speak_wait(text: str, timeout: float = 25) -> None:
    """문장을 끝까지 읽은 뒤에 반환한다. 실패해도 예외를 올리지 않는다."""
    spoken = str(text or "").strip()
    if not spoken:
        return
    _post_speak(spoken, wait=True, timeout=timeout)


def _post_speak(spoken: str, wait: bool, timeout: float) -> None:
    try:
        req = urllib.request.Request(
            TTS_URL,
            data=json.dumps({"text": spoken, "wait": wait}, ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=timeout).read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        print(f"[tts] {exc}", file=sys.stderr)
