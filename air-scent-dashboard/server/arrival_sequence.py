#!/usr/bin/env python3
"""arrival_sequence.py — 와드 도착 뒤 자동 청정·발향 (UI Jetson, ros_nav_bridge 가 부른다)

  /arrival_at_goal ─▶ ① 청정  : 청정기 ON (FAN ON)                     ARRIVAL_PURIFY_SEC 초
                      ② 발향  : 고른 향 채널 분무 (청정기는 꺼 둠)        ARRIVAL_SPRAY_SEC 초
                                (세 향 동시 — 펌웨어가 0.3초씩 늦춰 켬)
                                + 와드에 MQTT "SPRAY <초> <이름>" → 와드가 발향 중 + 3분 재출동 안 함
                      ③ 정리  : 청정기·발향 전부 끔 (FAN OFF + MIST OFF) → /go_around (정찰 재개)

- 화면(브라우저)이 꺼져 있어도 돈다. 중간에 멈추면(취소·정찰 재개·stop) ③의 '전부 끔'은 항상 한다.
- 향: ARRIVAL_SCENT=all(기본) 이면 세 향 모두. auto 면 화면에서 마지막으로 고른 블렌드, 없으면 lavender.
- 설정(환경변수): ARRIVAL_SEQUENCE(1/0) · ARRIVAL_PURIFY_SEC(10) · ARRIVAL_SPRAY_SEC(30) ·
  ARRIVAL_SCENT(all|auto|mint|lavender|orange) · ARRIVAL_PURIFIER_DURING_SPRAY(0) · FRAGRANCE_BRIDGE_URL · WARD_DEVICE_ID(ward-01)
- 화면 '발향 중지' → stop_mist(): 순서가 돌면 멈추고 전부 끔, 아니면 분무만 끔.
"""

from __future__ import annotations

import json
import os
import ssl
import sys
import threading
import time
import urllib.request

from tts_speak import PURIFY_AND_SPRAY_START, SPRAY_START, speak_wait

DEMO_SCENT_URL = os.environ.get("MQTT_DEMO_URL", "http://127.0.0.1:5175/api/mqtt/demo-scent")
CHANNELS = ("mint", "lavender", "orange")            # MIST1 · MIST2 · MIST3 (fragranceCommand.js MIST_CHANNELS)
WARD_NAME = {"mint": "peppermint", "lavender": "lavender", "orange": "orange"}
MIST_NAME = {"mint": "MNT", "lavender": "LVD", "orange": "ORG"}   # 펌웨어 air_control 2.1 채널 이름


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
        self.purify_sec = _env_float("ARRIVAL_PURIFY_SEC", 10.0)
        self.spray_sec = _env_float("ARRIVAL_SPRAY_SEC", 30.0)
        self.scent = os.environ.get("ARRIVAL_SCENT", "all").strip().lower()
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
              reason: str = "arrival", go_around: bool = True, spray_first: bool = False,
              channel: str | None = None) -> dict:
        if self.running():
            return {"ok": False, "error": "이미 진행 중", "status": self.status()}
        self._stop.clear()
        p = self.purify_sec if purify_sec is None else max(0.0, float(purify_sec))
        s = self.spray_sec if spray_sec is None else max(0.0, float(spray_sec))
        scent = channel if channel in CHANNELS else None
        self._thread = threading.Thread(
            target=self._run,
            args=(p, s, reason, go_around, bool(spray_first), scent),
            name="arrival-sequence",
            daemon=True,
        )
        self._thread.start()
        return {"ok": True, "purify_sec": 0 if spray_first else p, "spray_sec": s,
                "reason": reason, "spray_first": bool(spray_first), "channel": scent}

    def stop(self, wait: float = 5.0) -> dict:
        """진행 중이면 멈추고 청정기·발향을 끈다. 안 돌 때는 아무것도 안 한다 (/go_around 는 부른 쪽이 정한다)."""
        was = self.running()
        if was:
            import traceback
            caller = " ← ".join(f"{fr.name}:{fr.lineno}" for fr in traceback.extract_stack()[-4:-1])
            self.log(f"멈춤 요청 ({caller})")
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=wait)
        # 순서가 돌던 경우엔 스레드 finally 에서 이미 전부 끔.
        # 안 돌 때는 아무것도 끄지 않는다 — 손으로 켠 청정기·발향을 정찰 재개·취소가 꺼 버리던 문제(11/15).
        return {"ok": True, "was_running": was, "status": self.status()}

    def stop_mist(self) -> dict:
        """화면 '발향 중지'. 도착 후 순서가 돌면 멈추고 전부 끈다. 아니면 분무만 끈다."""
        if self.running():
            self.log("화면 발향 중지 → 도착 순서 멈춤")
            self._stop.set()
            if self._thread is not None:
                self._thread.join(timeout=8)  # 스레드 finally 에서 전부 끔
            return {"ok": True, "stopped_sequence": True}

        self.log("화면 발향 중지 → 분무만 끔")
        mist_off = {"fragranceOn": False, "fragranceDiffusing": False,
                    "fragranceChannels": {c: False for c in CHANNELS}}
        replies = self._fragrance_replies(["MIST OFF"], mist_off)
        ok = any(str(r).strip() == "MIST OFF" for r in replies)
        return {"ok": ok, "stopped_sequence": False, "command": "MIST OFF",
                "error": None if ok else "아두이노가 MIST OFF 를 답하지 않았습니다"}

    def _fragrance_replies(self, commands: list[str], state: dict) -> list:
        self.log(f"  → 아두이노 {commands}")
        body = json.dumps({"commands": commands, "state": state}).encode("utf-8")
        req = urllib.request.Request(f"{self.fragrance_url}/api/fragrance/command", data=body,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                res = json.loads(r.read().decode("utf-8"))
            self.log(f"  ← sent={res.get('sent')} replies={res.get('replies')}")
            return list(res.get("replies") or []) if res.get("sent") else []
        except Exception as exc:  # noqa: BLE001
            self.log(f"  ⚠ fragrance_bridge 연결 실패 {commands}: {exc!r}")
            return []

    # ------------------------------------------------------------ 순서
    def _run(self, purify_sec: float, spray_sec: float, reason: str, go_around: bool,
             spray_first: bool = False, channel: str | None = None) -> None:
        finished = False
        try:
            if spray_first:
                self.log(f"발향부터 시작 ({reason})")
            else:
                # 악취 도착: 청정기는 30초. 그 사이 시연 수치는 15%까지 내리고, 끝나면 설정된 향 시연으로 넘긴다.
                purify_sec = 30.0
                if not self._speak_then(PURIFY_AND_SPRAY_START):
                    return
                self._set("purify", until=time.time() + purify_sec, reason=reason)
                self.log(f"① 청정 시작 {purify_sec:.0f}초 ({reason})")
                self._fragrance(["FAN ON", "MIST OFF"], {"airPurifierOn": True, "fragranceOn": False,
                                            "fragranceDiffusing": False,
                                            "fragranceChannels": {c: False for c in CHANNELS}})
                self._signal_demo_purify()
                if self._stop.wait(purify_sec):
                    return
                scent_name = self._designated_channel()
                mist_cmd = "MIST " + MIST_NAME[scent_name]
                self.log(f"② 발향 시작 [{scent_name}] — 5초 뒤 시연")
                self._fragrance(
                    ["FAN OFF", mist_cmd],
                    {
                        "airPurifierOn": False,
                        "fragranceOn": True,
                        "fragranceDiffusing": True,
                        "fragranceChannels": {c: c == scent_name for c in CHANNELS},
                    },
                )
                self._set("spray", until=time.time() + 35.0, reason=reason, scent=[scent_name])
                self._ward_cmd(f"SPRAY 35 {WARD_NAME[scent_name]}")
                if self._stop.wait(5.0):
                    return
                self._post_demo(
                    {"scentRise": True, "label": scent_name},
                    f"{scent_name} 시연 시작, 95~96%까지",
                )
                if self._stop.wait(30.0):
                    return
                finished = True
                return

            chosen = [channel] if channel in CHANNELS else self._choose_channels()
            # 동시 발향: 펌웨어(air_control 2.1)가 채널을 0.3초씩 늦춰 켜서 돌입 전류를 피하고,
            # 켜진 채널은 끊김 없이 계속 분무한다.
            fan_cmd = "FAN ON" if self.purifier_during_spray else "FAN OFF"
            mist_cmd = "MIST ALL" if len(chosen) == len(CHANNELS) else "MIST " + "+".join(MIST_NAME[c] for c in chosen)
            if spray_first and not self._speak_then(SPRAY_START):
                return
            self.log(f"② 발향 {spray_sec:.0f}초 {chosen} → {fan_cmd}, {mist_cmd} (동시)")
            if len(chosen) == 1:
                self._show_demo_scent(chosen[0])
            else:
                self._signal_demo_rise()
            self._fragrance([fan_cmd, mist_cmd], {"airPurifierOn": self.purifier_during_spray, "fragranceOn": True,
                                                  "fragranceDiffusing": True,
                                                  "fragranceChannels": {c: c in chosen for c in CHANNELS}})
            # 첫 분무부터 spray_sec 를 센다
            self._set("spray", until=time.time() + spray_sec, reason=reason, scent=chosen)
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

    def _speak_then(self, text: str) -> bool:
        """안내를 끝까지 읽은 뒤에 다음 동작을 한다. 중간에 멈추면 False."""
        if self._stop.is_set():
            return False
        self.log(f"안내: {text}")
        speak_wait(text)
        return not self._stop.is_set()

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
        self._fragrance(["FAN OFF", "MIST OFF"], {"airPurifierOn": False, "fragranceOn": False, "fragranceDiffusing": False,
                                     "fragranceChannels": {c: False for c in CHANNELS}})

    def _show_demo_scent(self, channel: str) -> None:
        """분사 시작. 같은 향 시연이면 그 퍼센트에서 다시 올라가고, 아니면 그 향을 켠다."""
        self._post_demo({"label": channel, "rise": True}, f"시연 향 상승: {channel}")

    def _signal_demo_rise(self) -> None:
        self._post_demo({"rise": True}, "시연 퍼센트 상승")

    def _signal_demo_purify(self) -> None:
        self._post_demo({"purify": True}, "악취 시연 15%로 하강")

    def _post_demo(self, payload: dict, ok_log: str) -> None:
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            DEMO_SCENT_URL,
            data=body,
            headers={"Content-Type": "application/json"},
        )
        try:
            urllib.request.urlopen(req, timeout=2).read()
            self.log(ok_log)
        except Exception as exc:  # noqa: BLE001
            self.log(f"시연 향 표시 실패: {exc!r}")

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

    def _designated_channel(self) -> str:
        """화면에 잡아 둔 향 하나. 페퍼민트면 페퍼민트 시연 버튼과 같은 라벨."""
        if self.scent in CHANNELS:
            return self.scent
        try:
            with urllib.request.urlopen(f"{self.fragrance_url}/api/fragrance/state", timeout=5) as r:
                blend = (json.loads(r.read().decode("utf-8")).get("state") or {}).get("fragranceBlend") or {}
            best = max(CHANNELS, key=lambda name: float(blend.get(name) or 0))
            if float(blend.get(best) or 0) > 0:
                return best
        except Exception as exc:  # noqa: BLE001
            self.log(f"  설정 향 읽기 실패 → lavender: {exc!r}")
        return "lavender"

    def _choose_channels(self) -> list[str]:
        if self.scent == "all":
            return list(CHANNELS)
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
