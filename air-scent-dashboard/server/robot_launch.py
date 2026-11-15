#!/usr/bin/env python3
"""주행 Jetson 의 `ros2 launch third_impact impact_launch.py` 상태 감시 (읽기 전용).

2초마다 SSH(호스트 별칭 `robot`)로 프로세스와 launch 로그만 읽어 4가지로 나눈다.
  off       꺼짐       launch 프로세스 없음
  starting  켜지는 중  launch 는 있는데 필수 노드가 아직 다 안 뜸
  on        켜짐       launch + 필수 노드 모두 있음
  stopping  꺼지는 중  launch 가 종료 신호(Ctrl+C 등)를 받아 노드를 내리는 중
  unknown   확인 불가  SSH 실패 (주행 Jetson 꺼짐·연결 끊김)

화면 버튼으로 켜기·끄기도 한다 (팀 합의, 2026-11-10):
  start       꺼짐일 때만: ros2 launch third_impact impact_launch.py network:=jetson
  stop        켜짐·켜지는 중일 때: launch 에 SIGINT (터미널 Ctrl+C 와 같음)
  force_stop  꺼지는 중에 멈췄을 때: launch 프로세스 그룹에 SIGKILL
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time

HOST = os.environ.get("ROBOT_SSH_HOST", "robot")
POLL_SEC = float(os.environ.get("ROBOT_LAUNCH_POLL_SEC", "2"))
CONTROL_LOG = os.environ.get("ROBOT_LAUNCH_LOG", "/tmp/robot_launch_control.log")

# 주행 Jetson 에서 python3 로 실행. /proc 을 직접 읽어 pgrep 이 자기 셸을 잡는 문제를 피한다.
REMOTE_SCRIPT = r"""
import glob, json, os, time

REQUIRED = {
    "impact_node": "third_impact/impact_node",
    "localization_bootstrap": "third_impact/localization_bootstrap",
    "amcl": "nav2_amcl/amcl",
    "map_server": "nav2_map_server/map_server",
    "lidar": "sllidar_ros2/sllidar_node",
}
STOP_MARKERS = (
    "user interrupted with ctrl-c",
    "sending signal 'SIGINT'",
    "sending signal 'SIGTERM'",
    "sending signal 'SIGKILL'",
    "signal_handler(signum=",
)

hz = os.sysconf("SC_CLK_TCK")
with open("/proc/uptime") as f:
    uptime = float(f.read().split()[0])

launches = []
found = {k: False for k in REQUIRED}
for d in os.listdir("/proc"):
    if not d.isdigit():
        continue
    try:
        with open(f"/proc/{d}/cmdline", "rb") as f:
            argv = [a.decode("utf-8", "replace") for a in f.read().split(b"\0") if a]
    except Exception:
        continue
    if not argv:
        continue
    joined = " ".join(argv)
    is_launch = (
        len(argv) >= 4
        and os.path.basename(argv[0]).startswith("python")
        and argv[1].endswith("/ros2")
        and argv[2] == "launch"
        and "third_impact" in argv[3:5]
        and "impact_launch.py" in argv[3:6]
    )
    if is_launch:
        try:
            with open(f"/proc/{d}/stat") as f:
                start_ticks = int(f.read().rsplit(")", 1)[1].split()[19])
            age = uptime - start_ticks / hz
        except Exception:
            age = None
        launches.append({"pid": int(d), "args": argv[3:], "age_s": age})
        continue
    for key, needle in REQUIRED.items():
        if needle in joined:
            found[key] = True

stopping = False
log_tail = []
if launches:
    pid = max(launches, key=lambda x: x["pid"])["pid"]
    logs = glob.glob(os.path.expanduser(f"~/.ros/log/*-{pid}/launch.log"))
    if logs:
        try:
            with open(logs[0], "rb") as f:
                f.seek(0, 2)
                size = f.tell()
                f.seek(max(0, size - 65536))
                text = f.read().decode("utf-8", "replace")
            stopping = any(m in text for m in STOP_MARKERS)
        except Exception:
            pass

print(json.dumps({"launches": launches, "nodes": found, "stopping": stopping}))
"""

LAUNCH_CMD = "ros2 launch third_impact impact_launch.py network:=jetson"
REMOTE_LOG = "~/.ros/ui_impact_launch.log"

REMOTE_START = f"""
set -e
source /opt/ros/humble/setup.bash
source ~/capstone_final_ws/install/setup.bash
export ROS_DOMAIN_ID=3 RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
export CYCLONEDDS_URI=file://$HOME/.ros/cyclonedds.xml
cd ~/capstone_final_ws
# '&' 로 띄우면 bash 가 SIGINT 를 무시하게 만들어 끄기(Ctrl+C)가 안 먹는다.
# setsid -f 로 포그라운드 실행해 SIGINT 를 기본값으로 둔다.
setsid -f nohup {LAUNCH_CMD} > {REMOTE_LOG} 2>&1 < /dev/null
echo "started"
"""

# 실행 중인 impact_launch 를 찾아 신호를 보낸다. SIG 는 아래에서 바꿔 끼운다.
REMOTE_SIGNAL = r"""
import os, signal
mode = "__MODE__"
hits = []
for d in os.listdir("/proc"):
    if not d.isdigit():
        continue
    try:
        with open(f"/proc/{d}/cmdline", "rb") as f:
            argv = [a.decode("utf-8", "replace") for a in f.read().split(b"\0") if a]
    except Exception:
        continue
    if (
        len(argv) >= 4
        and os.path.basename(argv[0]).startswith("python")
        and argv[1].endswith("/ros2")
        and argv[2] == "launch"
        and "third_impact" in argv[3:5]
        and "impact_launch.py" in argv[3:6]
    ):
        hits.append(int(d))
def sigint_ignored(pid):
    try:
        with open(f"/proc/{pid}/status") as f:
            text = f.read()
        mask = int(text.split("SigIgn:")[1].split()[0], 16)
        return bool(mask >> (signal.SIGINT - 1) & 1)
    except Exception:
        return False

def children(pid):
    out = []
    for d in os.listdir("/proc"):
        if not d.isdigit():
            continue
        try:
            with open(f"/proc/{d}/stat") as f:
                ppid = int(f.read().rsplit(")", 1)[1].split()[1])
        except Exception:
            continue
        if ppid == pid:
            out.append(int(d))
    return out

kids_signalled = []
for pid in hits:
    try:
        if mode == "force":
            os.killpg(os.getpgid(pid), signal.SIGKILL)
            continue
        os.kill(pid, signal.SIGINT)
        # 백그라운드로 띄워 SIGINT 를 무시하는 launch 면, 노드들에 직접 SIGINT.
        # 노드가 모두 끝나면 launch 도 끝난다.
        if sigint_ignored(pid):
            for kid in children(pid):
                try:
                    os.kill(kid, signal.SIGINT)
                    kids_signalled.append(kid)
                except Exception as exc:
                    print("fail kid", kid, exc)
    except Exception as exc:
        print("fail", pid, exc)
print("signalled", mode, hits, "kids", len(kids_signalled))
"""

STATE_LABELS = {
    "off": "주행 꺼짐",
    "starting": "주행 켜지는 중",
    "on": "주행 켜짐",
    "stopping": "주행 꺼지는 중",
    "unknown": "주행 확인 불가",
}
NODE_LABELS = {
    "impact_node": "임무 노드",
    "localization_bootstrap": "위치 추정",
    "amcl": "AMCL",
    "map_server": "지도",
    "lidar": "LiDAR",
}


def _ssh_cmd() -> list[str]:
    return [
        "ssh",
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=4",
        # 연결을 재사용해 2초 폴링 부담을 줄인다
        "-o", "ControlMaster=auto",
        "-o", f"ControlPath=/tmp/robot-launch-ssh-{os.getuid()}",
        "-o", "ControlPersist=60",
        HOST,
        "python3", "-",
    ]


def classify(raw: dict) -> dict:
    launches = raw.get("launches") or []
    nodes = raw.get("nodes") or {}
    if not launches:
        return {"state": "off", "detail": "launch 가 실행 중이 아닙니다"}

    launch = max(launches, key=lambda x: x.get("pid") or 0)
    args = launch.get("args") or []
    network = next(
        (a.split(":=", 1)[1] for a in args if a.startswith("network:=")), "wifi"
    )
    missing = [NODE_LABELS.get(k, k) for k, ok in nodes.items() if not ok]
    out = {
        "pid": launch.get("pid"),
        "age_s": launch.get("age_s"),
        "network": network,
        "missing": missing,
        "multiple": len(launches) > 1,
    }

    if raw.get("stopping"):
        out.update(state="stopping", detail="종료 신호를 받아 노드를 내리는 중입니다")
    elif missing:
        out.update(state="starting", detail="아직 안 뜬 노드: " + ", ".join(missing))
    else:
        out.update(state="on", detail="필수 노드가 모두 실행 중입니다")
    if network != "jetson":
        out["warning"] = f"network:={network} 로 켜짐 — UI 와 통신하려면 network:=jetson 으로 켜야 합니다"
    return out


class RobotLaunchWatch:
    def __init__(self, link_check=None, log=None) -> None:
        # link_check(): UI 에서 주행 노드가 보이는지 (DDS). None 이면 생략.
        self._link_check = link_check
        self._log = log or (lambda msg: None)
        self._lock = threading.Lock()
        self._snap: dict = {
            "state": "unknown",
            "label": STATE_LABELS["unknown"],
            "detail": "확인 중",
            "checked_at": None,
        }
        self._last_state = None

    def start(self) -> "RobotLaunchWatch":
        threading.Thread(target=self._loop, name="robot-launch-watch", daemon=True).start()
        return self

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self._snap)

    def _poll_once(self) -> dict:
        try:
            proc = subprocess.run(
                _ssh_cmd(),
                input=REMOTE_SCRIPT.encode(),
                capture_output=True,
                timeout=8,
            )
            if proc.returncode != 0:
                err = proc.stderr.decode("utf-8", "replace").strip().splitlines()
                return {
                    "state": "unknown",
                    "detail": "주행 Jetson 에 접속하지 못했습니다"
                    + (f" ({err[-1][:120]})" if err else ""),
                }
            return classify(json.loads(proc.stdout.decode("utf-8", "replace")))
        except subprocess.TimeoutExpired:
            return {"state": "unknown", "detail": "주행 Jetson 응답 없음 (시간 초과)"}
        except Exception as exc:  # noqa: BLE001
            return {"state": "unknown", "detail": f"확인 실패: {exc}"}

    def control(self, action: str) -> dict:
        """화면 버튼: start / stop / force_stop. 지금 상태에 맞을 때만 실행."""
        state = self.snapshot().get("state")
        allowed = {
            "start": {"off"},
            "stop": {"on", "starting"},
            "force_stop": {"stopping"},
        }
        if action not in allowed:
            return {"ok": False, "error": f"알 수 없는 동작: {action}"}
        if state not in allowed[action]:
            return {
                "ok": False,
                "error": f"지금 상태({STATE_LABELS.get(state, state)})에서는 할 수 없습니다",
            }

        if action == "start":
            cmd, payload = _ssh_cmd()[:-2] + ["bash", "-s"], REMOTE_START
        else:
            mode = "force" if action == "force_stop" else "int"
            cmd, payload = _ssh_cmd(), REMOTE_SIGNAL.replace("__MODE__", mode)
        try:
            proc = subprocess.run(cmd, input=payload.encode(), capture_output=True, timeout=15)
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "주행 Jetson 응답 없음 (시간 초과)"}
        out = (proc.stdout + proc.stderr).decode("utf-8", "replace").strip()
        self._log(f"주행 launch {action} → rc={proc.returncode} {out[-200:]}")
        try:  # 화면 버튼 조작 기록 (vite 터미널이 안 보일 때 확인용)
            with open(CONTROL_LOG, "a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%H:%M:%S')} {action} rc={proc.returncode} {out[-200:]}\n")
        except OSError:
            pass
        if proc.returncode != 0:
            return {"ok": False, "error": out[-200:] or f"rc={proc.returncode}"}
        if action in ("stop", "force_stop"):
            self._stop_requested_at = time.time()
        if action == "stop" and "signalled int []" in out:
            return {"ok": False, "error": "끌 launch 를 찾지 못했습니다"}
        # 바로 다시 읽어 화면이 빨리 바뀌게
        threading.Thread(target=self._refresh_soon, daemon=True).start()
        return {"ok": True, "action": action, "output": out[-200:]}

    def _store(self, snap: dict) -> None:
        now = time.time()
        # 끄기를 보낸 뒤 90초 동안은 launch 가 남아 있으면 '꺼지는 중'
        stop_at = getattr(self, "_stop_requested_at", None)
        if stop_at is not None:
            if snap["state"] in ("off", "unknown") or now - stop_at > 90:
                self._stop_requested_at = None
            elif snap["state"] in ("on", "starting"):
                snap["state"] = "stopping"
                snap["detail"] = "끄기 신호를 보냈습니다. 노드를 내리는 중입니다"
        snap["label"] = STATE_LABELS.get(snap["state"], snap["state"])
        snap["checked_at"] = now
        with self._lock:
            prev = self._snap
            if snap["state"] == "stopping":
                # 꺼지는 중이 시작된 시각 (오래 멈추면 화면이 강제 끄기를 보여 줌)
                since = prev.get("stopping_since") if prev.get("state") == "stopping" else None
                snap["stopping_since"] = since or now
                snap["stopping_for_s"] = now - snap["stopping_since"]
            self._snap = snap

    def _refresh_soon(self) -> None:
        time.sleep(1.0)
        self._store(self._poll_once())

    def _loop(self) -> None:
        while True:
            snap = self._poll_once()
            if snap["state"] == "on" and self._link_check is not None:
                try:
                    snap["ui_link"] = bool(self._link_check())
                except Exception:  # noqa: BLE001
                    snap["ui_link"] = None
            self._store(snap)
            if snap["state"] != self._last_state:
                self._log(f"주행 launch 상태: {snap['label']} — {snap.get('detail', '')}")
                self._last_state = snap["state"]
            time.sleep(POLL_SEC)
