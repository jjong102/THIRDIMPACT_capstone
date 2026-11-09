#!/usr/bin/env python3
"""arrival_sequence.py — 와드 도착 뒤 자동 청정·발향 (UI Jetson, ros_nav_bridge 가 부른다)

  /arrival_at_goal ─▶ ① 청정  : 청정기 ON (ON000)                      ARRIVAL_PURIFY_SEC 초
                      ② 발향  : 고른 향 채널 분무 (청정기는 꺼 둠)        ARRIVAL_SPRAY_SEC 초
                                + 와드에 MQTT "SPRAY <초> <이름>" → 와드가 발향 중 + 3분 재출동 안 함
                      ③ 정리  : 청정기·발향 전부 끔 (OFF000) → /go_around (정찰 재개)

- 화면(브라우저)이 꺼져 있어도 돈다. 중간에 멈추면(취소·정찰 재개·stop) ③의 '전부 끔'은 항상 한다.
- 향: ARRIVAL_SCENT=auto 면 화면에서 마지막으로 고른 블렌드(fragrance_bridge state), 없으면 lavender.
- 설정(환경변수): ARRIVAL_SEQUENCE(1/0) · ARRIVAL_PURIFY_SEC(60) · ARRIVAL_SPRAY_SEC(60) ·
  ARRIVAL_SCENT(auto|mint|lavender|orange) · ARRIVAL_PURIFIER_DURING_SPRAY(0) · FRAGRANCE_BRIDGE_URL · WARD_DEVICE_ID(ward-01)
"""

from __future__ import annotations

import json
import os
import ssl
import sys
import threading
import time
import urllib.request

CHANNELS = ("mint", "lavender", "orange")            # MIST1 · MIST2 · MIST3 (fragranceCommand.js MIST_CHANNELS)
WARD_NAME = {"mint": "peppermint", "lavender": "lavender", "orange": "orange"}


def _env_float(name: str, default: float) -> float:
    try:
        return max(0.0, float(os.environ.get(name, default)))
    except ValueError:
        return default


class ArrivalSequence:
    def __init__(self, merge_ui, publish_go_around, log=None):
        self.merge_ui = merge_ui
        self.publish_go_around = publish_go_around
        self.log = log or self._default_log
        self.log_path = os.environ.get("ARRIVAL_LOG", "/tmp/arrival_sequence.log")
        self.enabled = os.environ.get("ARRIVAL_SEQUENCE", "1").strip() not in ("0", "false", "no", "")
        self.purify_sec = _env_float("ARRIVAL_PURIFY_SEC", 60.0)
        self.spray_sec = _env_float("ARRIVAL_SPRAY_SEC", 60.0)
        self.scent = os.environ.get("ARRIVAL_SCENT", "auto").strip().lower()
        self.purifier_during_spray = os.environ.get("ARRIVAL_PURIFIER_DURING_SPRAY", "0").strip() == "1"
        self.fragrance_url = os.environ.get("FRAGRANCE_BRIDGE_URL", "http://127.0.0.1:5174").rstrip("/")
        self.device = os.environ.get("WARD_DEVICE_ID", "ward-01")
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._status: dict = {"phase": "idle"}

    def _default_log(self, msg: str) -> None:
        line = f"{time.strftime('%H:%M:%S')} [arrival-seq] {msg}"
        print(line, file=sys.stderr, flush=True)
        try:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass

    # ------------------------------------------------------------ 외부에서 부르는 것
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def status(self) -> dict:
        with self._lock:
            return dict(self._status)

    def start(self, purify_sec: float | None = None, spray_sec: float | None = None,
              reason: str = "arrival", go_around: bool = True) -> dict:
        if self.running():
            return {"ok": False, "error": "이미 진행 중", "status": self.status()}
        self._stop.clear()
        p = self.purify_sec if purify_sec is None else max(0.0, float(purify_sec))
        s = self.spray_sec if spray_sec is None else max(0.0, float(spray_sec))
        self._thread = threading.Thread(target=self._run, args=(p, s, reason, go_around),
                                        name="arrival-sequence", daemon=True)
        self._thread.start()
        return {"ok": True, "purify_sec": p, "spray_sec": s, "reason": reason}

    def stop(self, wait: float = 5.0) -> dict:
        """진행 중이면 멈추고 청정기·발향을 끈다 (/go_around 는 보내지 않음 — 부른 쪽이 정한다)."""
        was = self.running()
        if was:
            import traceback
            caller = " ← ".join(f"{fr.name}:{fr.lineno}" for fr in traceback.extract_stack()[-4:-1])
            self.log(f"멈춤 요청 ({caller})")
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=wait)
        if not was:
            self._all_off("멈춤 요청")
        return {"ok": True, "was_running": was, "status": self.status()}

    # ------------------------------------------------------------ 순서
    def _run(self, purify_sec: float, spray_sec: float, reason: str, go_around: bool) -> None:
        finished = False
        try:
            self._set("purify", until=time.time() + purify_sec, reason=reason)
            self.log(f"① 청정 시작 {purify_sec:.0f}초 ({reason})")
            self._fragrance(["ON000"], {"airPurifierOn": True, "fragranceOn": False,
                                        "fragranceDiffusing": False,
                                        "fragranceChannels": {c: False for c in CHANNELS}})
            if self._stop.wait(purify_sec):
                return

            chosen = self._choose_channels()
            digits = "".join("9" if c in chosen else "0" for c in CHANNELS)
            cmd = ("ON" if self.purifier_during_spray else "OFF") + digits
            self._set("spray", until=time.time() + spray_sec, reason=reason, scent=chosen)
            self.log(f"② 발향 {spray_sec:.0f}초 {chosen} → {cmd}")
            self._fragrance([cmd], {"airPurifierOn": self.purifier_during_spray, "fragranceOn": True,
                                    "fragranceDiffusing": True,
                                    "fragranceChannels": {c: c in chosen for c in CHANNELS}})
            name = WARD_NAME[chosen[0]] if len(chosen) == 1 else "mix"
            self._ward_cmd(f"SPRAY {int(round(spray_sec))} {name}")
            if self._stop.wait(spray_sec):
                return
            finished = True
        except Exception as exc:  # noqa: BLE001
            self.log(f"오류: {exc!r}")
        finally:
            self._all_off("완료" if finished else "중단")
            if finished and go_around:
                try:
                    result = self.publish_go_around()
                    self.log(f"③ /go_around → {result.get('ok')}")
                except Exception as exc:  # noqa: BLE001
                    self.log(f"/go_around 실패: {exc!r}")
            self._set("done" if finished else "stopped", reason=reason)

    # ------------------------------------------------------------ 도우미
    def _set(self, phase: str, **extra) -> None:
        with self._lock:
            self._status = {"phase": phase, "at": time.time(), **extra}
            snap = dict(self._status)
        try:
            patch = {"arrivalSequence": snap}
            if phase in ("done", "stopped"):
                patch.update({"isNavigating": False, "scentMission": False})
            self.merge_ui(patch)
        except Exception as exc:  # noqa: BLE001
            self.log(f"화면 상태 갱신 실패: {exc!r}")

    def _all_off(self, why: str) -> None:
        self.log(f"청정기·발향 전부 끔 ({why})")
        self._fragrance(["OFF000"], {"airPurifierOn": False, "fragranceOn": False, "fragranceDiffusing": False,
                                     "fragranceChannels": {c: False for c in CHANNELS}})

    def _fragrance(self, commands: list[str], state: dict) -> bool:
        self.log(f"  → 아두이노 {commands}")
        body = json.dumps({"commands": commands, "state": state}).encode("utf-8")
        req = urllib.request.Request(f"{self.fragrance_url}/api/fragrance/command", data=body,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                res = json.loads(r.read().decode("utf-8"))
            self.log(f"  ← sent={res.get('sent')} replies={res.get('replies')}")
            return bool(res.get("sent"))
        except Exception as exc:  # noqa: BLE001
            self.log(f"  ⚠ fragrance_bridge 연결 실패 {commands}: {exc!r}")
            return False

    def _choose_channels(self) -> list[str]:
        if self.scent in CHANNELS:
            return [self.scent]
        try:
            with urllib.request.urlopen(f"{self.fragrance_url}/api/fragrance/state", timeout=5) as r:
                blend = (json.loads(r.read().decode("utf-8")).get("state") or {}).get("fragranceBlend") or {}
            chosen = [c for c in CHANNELS if float(blend.get(c) or 0) > 0]
            if chosen:
                return chosen
        except Exception as exc:  # noqa: BLE001
            self.log(f"  화면 향 읽기 실패 → 기본 lavender: {exc!r}")
        return ["lavender"]

    def _ward_cmd(self, text: str) -> None:
        """와드에 명령 1개 (retained 아님). 실패해도 순서는 계속한다."""
        broker = os.environ.get("MQTT_BROKER", "")
        if not broker:
            self.log("  와드 SPRAY 생략 (MQTT_BROKER 없음)")
            return
        try:
            import paho.mqtt.client as mqtt
            c = mqtt.Client(client_id=f"ArrivalSeq_{os.getpid()}", protocol=mqtt.MQTTv311)
            c.username_pw_set(os.environ.get("MQTT_USER", ""), os.environ.get("MQTT_PASS", ""))
            c.tls_set(cert_reqs=ssl.CERT_NONE)
            c.tls_insecure_set(True)
            c.connect(broker, int(os.environ.get("MQTT_PORT", "8883")), 30)
            c.loop_start()
            info = c.publish(f"diffuser/{self.device}/cmd", text, qos=1, retain=False)
            info.wait_for_publish(timeout=5)
            c.loop_stop()
            c.disconnect()
            self.log(f"  와드 ← {text}")
        except Exception as exc:  # noqa: BLE001
            self.log(f"  와드 명령 실패 ({text}): {exc!r}")
