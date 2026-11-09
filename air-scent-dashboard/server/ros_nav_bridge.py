#!/usr/bin/env python3
"""HTTP ↔ 다른 젯슨 임무 노드.

사람이 넣는 명령은 Empty 토픽이다.
  /go_to_goal, /go_around, /save_goal,
  /third_impact/save_boundary, /third_impact/save_region

화면 반응은 임무 노드가 보내는 토픽을 본다.
  /arrival_at_goal, /third_impact/status, /third_impact/events,
  /third_impact/localization, /third_impact/manual_control,
  /third_impact/goal, /amcl_pose

/cmd_vel 은 임무 노드가 낸다. 이 브리지는 속도를 덮어쓰지 않는다.
위치 추정만 /initialpose 로 위치 추정 노드에 넘긴다.
"""

from __future__ import annotations

import json
import math
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from arrival_sequence import ArrivalSequence  # noqa: E402  도착 후 자동 청정·발향

PORT = int(os.environ.get("ROS_NAV_BRIDGE_PORT", "5179"))
INITIAL_TOPIC = os.environ.get("ROS_INITIAL_POSE_TOPIC", "initialpose")
AMCL_TOPIC = os.environ.get("ROS_AMCL_POSE_TOPIC", "amcl_pose")
FRAME_ID = os.environ.get("ROS_GOAL_FRAME_ID", "map")
GO_TO_GOAL_TOPIC = os.environ.get("ROS_GO_TO_GOAL_TOPIC", "go_to_goal")
GO_AROUND_TOPIC = os.environ.get("ROS_GO_AROUND_TOPIC", "go_around")
SAVE_GOAL_TOPIC = os.environ.get("ROS_SAVE_GOAL_TOPIC", "save_goal")
SAVE_BOUNDARY_TOPIC = os.environ.get(
    "ROS_SAVE_BOUNDARY_TOPIC", "third_impact/save_boundary"
)
SAVE_REGION_TOPIC = os.environ.get(
    "ROS_SAVE_REGION_TOPIC", "third_impact/save_region"
)
ARRIVAL_TOPIC = os.environ.get("ROS_ARRIVAL_TOPIC", "arrival_at_goal")
IMPACT_STATUS_TOPIC = os.environ.get(
    "ROS_IMPACT_STATUS_TOPIC", "third_impact/status"
)
IMPACT_EVENTS_TOPIC = os.environ.get(
    "ROS_IMPACT_EVENTS_TOPIC", "third_impact/events"
)
LOCALIZATION_TOPIC = os.environ.get(
    "ROS_LOCALIZATION_TOPIC", "third_impact/localization"
)
MANUAL_TOPIC = os.environ.get(
    "ROS_MANUAL_CONTROL_TOPIC", "third_impact/manual_control"
)
IMPACT_GOAL_TOPIC = os.environ.get("ROS_IMPACT_GOAL_TOPIC", "third_impact/goal")
EMPTY_COMMANDS = {
    "go_to_goal": GO_TO_GOAL_TOPIC,
    "go_around": GO_AROUND_TOPIC,
    "save_goal": SAVE_GOAL_TOPIC,
    "save_boundary": SAVE_BOUNDARY_TOPIC,
    "save_region": SAVE_REGION_TOPIC,
}
UI_STATE_PATH = Path(
    os.environ.get(
        "ROS_NAV_UI_STATE_PATH",
        Path(__file__).resolve().parent / ".move-ui-state.json",
    )
)
MAX_UI_WARDS = 1

# RViz 2D Pose Estimate defaults (x, y, yaw variance)
DEFAULT_COVARIANCE = [0.0] * 36
DEFAULT_COVARIANCE[0] = 0.25
DEFAULT_COVARIANCE[7] = 0.25
DEFAULT_COVARIANCE[35] = 0.06853891945200942

rclpy = None
PoseStamped = None
PoseWithCovarianceStamped = None
Empty = None
String = None
Bool = None
QoSProfile = None
ReliabilityPolicy = None
HistoryPolicy = None
DurabilityPolicy = None

_node = None
_initial_pub = None
_amcl_sub = None
_empty_pubs: dict = {}
_impact_subs: list = []
_interfaces_ready = False
_lock = threading.Lock()
_init_lock = threading.Lock()
_spin_thread = None
_last_error: str | None = None
_goal_count = 0
_initial_count = 0
_cancel_count = 0
_command_counts: dict[str, int] = {}
_amcl_pose: dict | None = None
_manual_control = False
_arrival_hold_until = 0.0
_last_arrival_at = 0.0
_last_status_key: tuple | None = None
_pending_motion: dict | None = None
_impact_goal: dict | None = None

ALLOWED_NAV_STATUS = {
    "EXECUTING",
    "XY_ARRIVED",
    "SUCCEEDED",
    "CANCELED",
    "ABORTED",
}
LOC_STATUS_VALUES = {"not_localized", "localized"}
MISSION_PHASES = {"patrol", "return", "wait", "setup", "idle", "manual"}
ARRIVAL_HOLD_SEC = 4.0


def _import_ros() -> bool:
    global rclpy, PoseStamped, PoseWithCovarianceStamped
    global Empty, String, Bool
    global QoSProfile, ReliabilityPolicy, HistoryPolicy, DurabilityPolicy
    try:
        import rclpy as _rclpy
        from geometry_msgs.msg import PoseStamped as _PoseStamped
        from geometry_msgs.msg import PoseWithCovarianceStamped as _PoseCov
        from rclpy.qos import DurabilityPolicy as _DurabilityPolicy
        from rclpy.qos import HistoryPolicy as _HistoryPolicy
        from rclpy.qos import QoSProfile as _QoSProfile
        from rclpy.qos import ReliabilityPolicy as _ReliabilityPolicy
        from std_msgs.msg import Bool as _Bool
        from std_msgs.msg import Empty as _Empty
        from std_msgs.msg import String as _String

        rclpy = _rclpy
        PoseStamped = _PoseStamped
        PoseWithCovarianceStamped = _PoseCov
        Empty = _Empty
        String = _String
        Bool = _Bool
        QoSProfile = _QoSProfile
        ReliabilityPolicy = _ReliabilityPolicy
        HistoryPolicy = _HistoryPolicy
        DurabilityPolicy = _DurabilityPolicy
        return True
    except Exception as exc:  # noqa: BLE001
        global _last_error
        _last_error = f"rclpy import failed: {exc}"
        print(f"[ros-nav-bridge] {_last_error}", file=sys.stderr)
        return False


def _quat_to_yaw(x: float, y: float, z: float, w: float) -> float:
    return math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))


def _on_amcl_pose(msg) -> None:
    global _amcl_pose
    pose = msg.pose.pose
    yaw = _quat_to_yaw(
        pose.orientation.x,
        pose.orientation.y,
        pose.orientation.z,
        pose.orientation.w,
    )
    with _lock:
        _amcl_pose = {
            "live": True,
            "source": "amcl",
            "frame_id": msg.header.frame_id or FRAME_ID,
            "x_m": float(pose.position.x),
            "y_m": float(pose.position.y),
            "yaw": float(yaw),
            "stamp": time.time(),
        }


def _topic_name(name: str) -> str:
    return name if str(name).startswith("/") else f"/{name}"


def _merge_if_changed(patch: dict) -> bool:
    current = snapshot_ui()
    changed = any(current.get(key) != value for key, value in patch.items())
    if changed:
        merge_ui(patch)
    return changed


def _phase_from_text(text: str) -> str | None:
    blob = str(text or "").lower()
    if any(token in blob for token in ("setup", "setting", "config", "설정")):
        return "setup"
    if any(token in blob for token in ("return", "go_to_goal", "homing", "복귀")):
        return "return"
    if any(token in blob for token in ("patrol", "around", "scout", "explore", "정찰")):
        return "patrol"
    if any(token in blob for token in ("wait", "block", "hold", "pause", "대기", "차단")):
        return "wait"
    if any(token in blob for token in ("manual", "joystick", "수동")):
        return "manual"
    if any(token in blob for token in ("idle", "stop", "정지", "none")):
        return "idle"
    return None


def _as_bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, dict):
        for key in ("ok", "ready", "aligned", "success", "localized"):
            if isinstance(value.get(key), bool):
                return bool(value.get(key))
        missing = value.get("missing") or value.get("missing_topics")
        if isinstance(missing, list):
            return len(missing) == 0
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return bool(value)
    if isinstance(value, str):
        low = value.strip().lower()
        if low in {"1", "true", "yes", "ok", "ready", "aligned", "success"}:
            return True
        if low in {"0", "false", "no", "not_ready", "fail", "failed"}:
            return False
    return None


def _pick(payload: dict, keys: tuple[str, ...]) -> object:
    for key in keys:
        if key in payload:
            return payload[key]
    return None


def _interpret_localization(raw: str) -> str | None:
    text = str(raw or "").strip()
    if not text:
        return None
    parsed: object = text
    if text[:1] in "[{":
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = text
    if isinstance(parsed, dict):
        flag = _as_bool(
            _pick(
                parsed,
                ("localized", "success", "ok", "ready", "result", "status", "state"),
            )
        )
        if flag is True:
            return "localized"
        if flag is False:
            return "not_localized"
        parsed = json.dumps(parsed, ensure_ascii=False)
    blob = str(parsed).strip().lower()
    if blob in {"localized", "ok", "success", "succeeded", "found", "done", "ready"}:
        return "localized"
    if blob in {
        "not_localized",
        "lost",
        "failed",
        "failure",
        "searching",
        "search",
        "init",
        "initializing",
        "none",
        "unknown",
    }:
        return "not_localized"
    if "not" in blob and "local" in blob:
        return "not_localized"
    if any(token in blob for token in ("fail", "failed", "failure", "lost", "실패", "못찾")):
        return "not_localized"
    if any(token in blob for token in ("local", "success", "성공", "완료")):
        return "localized"
    if any(token in blob for token in ("search", "searching", "탐색", "initializing")):
        return "not_localized"
    return None


def _status_fields(payload: object) -> tuple[str | None, bool | None, bool | None]:
    if isinstance(payload, str):
        return _phase_from_text(payload), None, None
    if not isinstance(payload, dict):
        return None, None, None
    if payload.get("setup") is True or payload.get("setting_mode") is True:
        phase = "setup"
    else:
        mission = _pick(payload, ("mission", "task", "phase", "mode", "state", "임무"))
        if isinstance(mission, dict):
            mission = " ".join(
                str(mission.get(key))
                for key in ("name", "state", "mode", "phase", "status")
                if mission.get(key) is not None
            )
        phase = _phase_from_text(str(mission or ""))
        if phase is None:
            phase = _phase_from_text(json.dumps(payload, ensure_ascii=False))
    ready = _as_bool(_pick(payload, ("ready", "readiness", "prepared", "준비")))
    aligned = _as_bool(_pick(payload, ("alignment", "align", "aligned", "정렬")))
    return phase, ready, aligned


def _select_home_ward(patch: dict) -> None:
    state = snapshot_ui()
    if state.get("selectedId"):
        return
    wards = state.get("wards") or []
    if wards and isinstance(wards[0], dict) and wards[0].get("id"):
        patch["selectedId"] = str(wards[0]["id"])


def _on_arrival(_msg) -> None:
    global _arrival_hold_until, _last_arrival_at
    now = time.time()
    if now - _last_arrival_at < 1.0:
        return
    _last_arrival_at = now
    _arrival_hold_until = now + ARRIVAL_HOLD_SEC
    state = snapshot_ui()
    patch = {
        "navStatus": "SUCCEEDED",
        "isNavigating": False,
        "xyArrived": True,
        "goalStatus": "succeeded",
        "scentMission": True,
        "missionPhase": "wait",
        "arrivalId": int(state.get("arrivalId") or 0) + 1,
    }
    _select_home_ward(patch)
    seq = _get_sequence()
    if seq.enabled:
        patch["scentMission"] = False   # 브라우저의 옛 도착 흐름(useScentMission)과 겹치지 않게
    merge_ui(patch)
    print("[ros-nav-bridge] arrival_at_goal", file=sys.stderr)
    if seq.enabled:
        print(f"[ros-nav-bridge] 도착 → 자동 청정·발향 {seq.start(reason='arrival')}", file=sys.stderr)


def _motion_patch(phase: str | None) -> dict:
    patch: dict = {}
    if _manual_control or phase == "manual":
        patch["isNavigating"] = False
        patch["missionPhase"] = "manual"
        return patch
    if phase == "return":
        patch["isNavigating"] = True
        patch["navStatus"] = "EXECUTING"
        patch["scentMission"] = True
        patch["xyArrived"] = False
        _select_home_ward(patch)
        return patch
    if phase == "patrol":
        patch["isNavigating"] = False
        patch["navStatus"] = None
        return patch
    if phase == "wait":
        patch["isNavigating"] = False
        return patch
    patch["isNavigating"] = False
    patch["navStatus"] = None
    return patch


def _flush_arrival_hold() -> None:
    global _pending_motion
    if _pending_motion is None or time.time() < _arrival_hold_until:
        return
    pending = _pending_motion
    _pending_motion = None
    _merge_if_changed(pending)


def _on_impact_status(msg) -> None:
    global _last_status_key, _pending_motion
    raw = str(getattr(msg, "data", "") or "").strip()
    if not raw:
        return
    payload: object = raw
    if raw[:1] in "[{":
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = raw
    phase, ready, aligned = _status_fields(payload)
    if _manual_control:
        phase = "manual"
    key = (phase, ready, aligned, _manual_control)
    if key == _last_status_key and _pending_motion is None:
        return
    _last_status_key = key
    shown = phase if phase in MISSION_PHASES else None
    display = {
        "missionPhase": "manual" if _manual_control else shown,
        "missionReady": ready,
        "missionAligned": aligned,
        "manualControl": _manual_control,
    }
    motion = _motion_patch(shown)
    if time.time() < _arrival_hold_until:
        _pending_motion = motion
        changed = _merge_if_changed(display)
    else:
        _pending_motion = None
        changed = _merge_if_changed({**display, **motion})
    if changed:
        print(
            f"[ros-nav-bridge] status phase={display.get('missionPhase')} "
            f"ready={ready} aligned={aligned}",
            file=sys.stderr,
        )


def _on_impact_event(msg) -> None:
    text = str(getattr(msg, "data", "") or "").strip()
    if not text:
        return
    if text[:1] in "[{":
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            picked = _pick(payload, ("message", "event", "text", "name", "status"))
            if picked:
                text = str(picked)
    _merge_if_changed({"lastEvent": text[:180]})


def _on_localization(msg) -> None:
    raw = str(getattr(msg, "data", "") or "").strip()
    loc = _interpret_localization(raw)
    patch = {"localizationText": raw[:180]}
    if loc in LOC_STATUS_VALUES:
        patch["locStatus"] = loc
    if _merge_if_changed(patch):
        print(f"[ros-nav-bridge] localization {loc or raw[:80]}", file=sys.stderr)


def _on_manual_control(msg) -> None:
    global _manual_control, _last_status_key
    value = bool(getattr(msg, "data", False))
    if value == _manual_control:
        return
    _manual_control = value
    _last_status_key = None
    patch = {"manualControl": value}
    if value:
        patch["missionPhase"] = "manual"
        patch["isNavigating"] = False
    _merge_if_changed(patch)
    print(f"[ros-nav-bridge] manual_control {value}", file=sys.stderr)


def _on_impact_goal(msg) -> None:
    global _impact_goal
    pose = msg.pose
    yaw = _quat_to_yaw(
        pose.orientation.x,
        pose.orientation.y,
        pose.orientation.z,
        pose.orientation.w,
    )
    goal = {
        "frame_id": msg.header.frame_id or FRAME_ID,
        "x": round(float(pose.position.x), 3),
        "y": round(float(pose.position.y), 3),
        "yaw": round(float(yaw), 3),
    }
    if goal == _impact_goal:
        return
    _impact_goal = goal
    _merge_if_changed({"goalPose": goal})
    print(
        f"[ros-nav-bridge] goal x={goal['x']:.3f} y={goal['y']:.3f}",
        file=sys.stderr,
    )


def _ensure_mission_interfaces() -> None:
    global _interfaces_ready

    if _node is None or _interfaces_ready:
        return

    assert Empty is not None
    assert String is not None
    assert Bool is not None
    assert PoseStamped is not None
    assert QoSProfile is not None

    for name, topic in EMPTY_COMMANDS.items():
        if name not in _empty_pubs:
            _empty_pubs[name] = _node.create_publisher(Empty, topic, 10)
            print(f"[ros-nav-bridge] → /{topic}", file=sys.stderr)

    volatile = QoSProfile(
        depth=10,
        reliability=ReliabilityPolicy.RELIABLE,
        durability=DurabilityPolicy.VOLATILE,
        history=HistoryPolicy.KEEP_LAST,
    )
    _impact_subs.extend(
        [
            _node.create_subscription(
                Empty, _topic_name(ARRIVAL_TOPIC), _on_arrival, volatile
            ),
            _node.create_subscription(
                String, _topic_name(IMPACT_EVENTS_TOPIC), _on_impact_event, volatile
            ),
            _node.create_subscription(
                String, _topic_name(IMPACT_STATUS_TOPIC), _on_impact_status, volatile
            ),
            _node.create_subscription(
                String, _topic_name(LOCALIZATION_TOPIC), _on_localization, volatile
            ),
            _node.create_subscription(
                Bool, _topic_name(MANUAL_TOPIC), _on_manual_control, volatile
            ),
            _node.create_subscription(
                PoseStamped, _topic_name(IMPACT_GOAL_TOPIC), _on_impact_goal, volatile
            ),
        ]
    )
    print(
        "[ros-nav-bridge] ← "
        f"/{ARRIVAL_TOPIC} /{IMPACT_STATUS_TOPIC} /{IMPACT_EVENTS_TOPIC} "
        f"/{LOCALIZATION_TOPIC} /{MANUAL_TOPIC} /{IMPACT_GOAL_TOPIC}",
        file=sys.stderr,
    )
    _interfaces_ready = True


def _ros_handles_alive() -> bool:
    return (
        rclpy is not None
        and rclpy.ok()
        and _node is not None
        and _empty_pubs.get("go_to_goal") is not None
        and _initial_pub is not None
        and _amcl_sub is not None
    )


def _reset_ros_handles() -> None:
    global _node, _initial_pub, _amcl_sub, _interfaces_ready
    _node = None
    _initial_pub = None
    _amcl_sub = None
    _empty_pubs.clear()
    _impact_subs.clear()
    _interfaces_ready = False


def ensure_ros() -> bool:
    global _node, _initial_pub, _amcl_sub, _spin_thread, _last_error

    if _ros_handles_alive():
        return True

    if _initial_pub is not None or _empty_pubs:
        print("[ros-nav-bridge] ROS 컨텍스트가 끊겨 다시 붙입니다", file=sys.stderr)
        _reset_ros_handles()

    if rclpy is None and not _import_ros():
        return False

    assert rclpy is not None
    assert PoseWithCovarianceStamped is not None
    assert QoSProfile is not None
    assert DurabilityPolicy is not None

    with _init_lock:
        if (
            _empty_pubs.get("go_to_goal") is not None
            and _initial_pub is not None
            and _amcl_sub is not None
        ):
            return True

        try:
            if not rclpy.ok():
                rclpy.init(args=None)

            _node = rclpy.create_node("dashboard_nav_bridge")
            _initial_pub = _node.create_publisher(
                PoseWithCovarianceStamped,
                INITIAL_TOPIC,
                QoSProfile(
                    depth=1,
                    reliability=ReliabilityPolicy.RELIABLE,
                    history=HistoryPolicy.KEEP_LAST,
                ),
            )
            _ensure_mission_interfaces()

            amcl_qos = QoSProfile(
                depth=1,
                reliability=ReliabilityPolicy.RELIABLE,
                durability=DurabilityPolicy.TRANSIENT_LOCAL,
                history=HistoryPolicy.KEEP_LAST,
            )
            _amcl_sub = _node.create_subscription(
                PoseWithCovarianceStamped,
                AMCL_TOPIC,
                _on_amcl_pose,
                amcl_qos,
            )
            _node.create_timer(0.5, _flush_arrival_hold)

            def _spin() -> None:
                try:
                    rclpy.spin(_node)
                except Exception:  # noqa: BLE001
                    pass

            _spin_thread = threading.Thread(target=_spin, daemon=True)
            _spin_thread.start()
            _last_error = None
            print(
                f"[ros-nav-bridge] → /{GO_TO_GOAL_TOPIC} /{GO_AROUND_TOPIC} "
                f"/ {INITIAL_TOPIC}, ← /{AMCL_TOPIC}",
                file=sys.stderr,
            )
            return True
        except Exception as exc:  # noqa: BLE001
            _last_error = str(exc)
            _reset_ros_handles()
            print(f"[ros-nav-bridge] init failed: {_last_error}", file=sys.stderr)
            return False


def yaw_to_quaternion(yaw: float) -> tuple[float, float, float, float]:
    half = yaw * 0.5
    return (0.0, 0.0, math.sin(half), math.cos(half))


def publish_goal(x: float = 0.0, y: float = 0.0, yaw: float = 0.0, frame_id: str = FRAME_ID) -> bool:
    """지도에서 고른 좌표 대신, 저장된 목표로 복귀하는 Empty를 보낸다."""
    del x, y, yaw, frame_id
    result = publish_empty_command("go_to_goal")
    if not result.get("ok"):
        return False
    state = snapshot_ui()
    patch = {
        "isNavigating": True,
        "navStatus": "EXECUTING",
        "scentMission": True,
        "xyArrived": False,
        "missionPhase": "return",
    }
    _select_home_ward(patch)
    if not state.get("selectedId") and patch.get("selectedId"):
        pass
    merge_ui(patch)
    global _goal_count
    with _lock:
        _goal_count += 1
    return True


def publish_initial_pose(
    x: float, y: float, yaw: float = 0.0, frame_id: str = FRAME_ID
) -> bool:
    global _initial_count, _last_error, _amcl_pose

    if not ensure_ros():
        return False

    assert _node is not None
    assert _initial_pub is not None
    assert PoseWithCovarianceStamped is not None

    msg = PoseWithCovarianceStamped()
    msg.header.stamp = _node.get_clock().now().to_msg()
    msg.header.frame_id = frame_id or FRAME_ID
    msg.pose.pose.position.x = float(x)
    msg.pose.pose.position.y = float(y)
    msg.pose.pose.position.z = 0.0
    qx, qy, qz, qw = yaw_to_quaternion(float(yaw))
    msg.pose.pose.orientation.x = qx
    msg.pose.pose.orientation.y = qy
    msg.pose.pose.orientation.z = qz
    msg.pose.pose.orientation.w = qw
    msg.pose.covariance = list(DEFAULT_COVARIANCE)

    try:
        _initial_pub.publish(msg)
        _initial_pub.publish(msg)
    except Exception as exc:  # noqa: BLE001
        if "context is invalid" not in str(exc).lower():
            _last_error = str(exc)
            raise
        print("[ros-nav-bridge] initialpose 컨텍스트 끊김, 재연결", file=sys.stderr)
        _reset_ros_handles()
        if not ensure_ros():
            _last_error = str(exc)
            return False
        assert _initial_pub is not None
        _initial_pub.publish(msg)
        _initial_pub.publish(msg)

    with _lock:
        _initial_count += 1
        # UI에 바로 보이게 낙관적 위치 반영 (amcl 갱신 전)
        _amcl_pose = {
            "live": True,
            "source": "initialpose",
            "frame_id": frame_id or FRAME_ID,
            "x_m": float(x),
            "y_m": float(y),
            "yaw": float(yaw),
            "stamp": time.time(),
        }

    print(
        f"[ros-nav-bridge] → /{INITIAL_TOPIC} x={x:.3f} y={y:.3f} yaw={yaw:.3f}",
        file=sys.stderr,
    )
    _last_error = None
    return True


def publish_empty_command(name: str) -> dict:
    global _last_error

    command = str(name or "").strip()
    topic = EMPTY_COMMANDS.get(command)
    if topic is None:
        return {"ok": False, "error": f"unknown command: {command}"}

    if not ensure_ros():
        return {"ok": False, "error": _last_error or "ros not ready"}

    pub = _empty_pubs.get(command)
    if pub is None or Empty is None:
        return {"ok": False, "error": "command publisher missing"}

    try:
        pub.publish(Empty())
    except Exception as exc:  # noqa: BLE001
        if "context is invalid" not in str(exc).lower():
            _last_error = str(exc)
            return {"ok": False, "error": str(exc)}
        _reset_ros_handles()
        if not ensure_ros():
            _last_error = str(exc)
            return {"ok": False, "error": str(exc)}
        pub = _empty_pubs.get(command)
        if pub is None:
            return {"ok": False, "error": "command publisher missing"}
        pub.publish(Empty())

    with _lock:
        _command_counts[command] = int(_command_counts.get(command) or 0) + 1
        count = _command_counts[command]
    print(f"[ros-nav-bridge] → /{topic} Empty ({count})", file=sys.stderr)
    _last_error = None
    return {
        "ok": True,
        "command": command,
        "topic": f"/{topic}",
        "type": "std_msgs/msg/Empty",
        "published": count,
    }


def cancel_navigation() -> dict:
    """복귀를 멈추고 정찰을 재개한다. 속도 토픽은 건드리지 않는다."""
    global _cancel_count
    _get_sequence().stop()
    result = publish_empty_command("go_around")
    with _lock:
        _cancel_count += 1
    if result.get("ok"):
        merge_ui(
            {
                "isNavigating": False,
                "navStatus": "CANCELED",
                "scentMission": False,
                "missionPhase": "patrol",
            }
        )
    result["cancelled"] = [GO_AROUND_TOPIC]
    return result


def publish_diffusion_complete() -> dict:
    """분사가 끝나면 차단 대기를 풀고 정찰을 재개한다."""
    _get_sequence().stop()
    result = publish_empty_command("go_around")
    if result.get("ok"):
        merge_ui(
            {
                "isNavigating": False,
                "navStatus": None,
                "scentMission": False,
                "missionPhase": "patrol",
            }
        )
    return result


def get_amcl_pose() -> dict:
    ensure_ros()
    with _lock:
        if _amcl_pose is None:
            return {
                "live": False,
                "source": "amcl",
                "error": "no amcl_pose yet",
            }
        return dict(_amcl_pose)


def _parse_pose_body(raw: bytes) -> tuple[float, float, float, str]:
    body = json.loads(raw.decode("utf-8") if raw else "{}")
    x = float(body["x"])
    y = float(body["y"])
    yaw = float(body.get("yaw", 0.0))
    frame_id = str(body.get("frame_id") or FRAME_ID)
    return x, y, yaw, frame_id


def default_ui_state() -> dict:
    return {
        "rev": 0,
        "wards": [],
        "selectedId": None,
        "dropPin": None,
        "navGoal": None,
        "isNavigating": False,
        "navStatus": None,
        "goalStatus": None,
        "xyArrived": False,
        "locStatus": None,
        "missionPhase": None,
        "missionReady": None,
        "missionAligned": None,
        "manualControl": False,
        "arrivalId": 0,
        "lastEvent": None,
        "localizationText": None,
        "goalPose": None,
        "mode": "select",
        "poseEstimating": False,
        "scentMission": False,
        "fragranceSpraying": False,
        "fragranceWardId": None,
        "updatedAt": None,
    }


ROOM_NAME_ALIASES = {
    "room_1": "거실",
    "room1": "거실",
    "ward-home-1": "거실",
    "ward_home_1": "거실",
    "room_2": "주방",
    "room2": "주방",
    "ward-home-2": "주방",
    "ward_home_2": "주방",
}

ROOM_NAME_FROM_LABEL = {
    "room1": "거실",
    "room_1": "거실",
    "302호": "거실",
    "room2": "주방",
    "room_2": "주방",
    "집2": "주방",
}


def _display_ward_name(ward_id: str, name: str) -> str:
    key = str(ward_id or "").strip().lower().replace("-", "_")
    if key in ROOM_NAME_ALIASES:
        return ROOM_NAME_ALIASES[key]
    pretty = str(name or "").strip()
    compact = pretty.lower().replace(" ", "").replace("_", "")
    if compact in ROOM_NAME_FROM_LABEL:
        return ROOM_NAME_FROM_LABEL[compact]
    if pretty in ROOM_NAME_FROM_LABEL:
        return ROOM_NAME_FROM_LABEL[pretty]
    return pretty[:12] or "집"


def _normalize_ward(item: object) -> dict | None:
    if not isinstance(item, dict):
        return None
    ward_id = str(item.get("id") or "").strip()
    if not ward_id:
        return None
    try:
        x = float(item.get("x"))
        y = float(item.get("y"))
    except (TypeError, ValueError):
        return None
    name = _display_ward_name(ward_id, str(item.get("name") or "집"))
    ward = {"id": ward_id, "name": name, "x": x, "y": y}
    sensor = item.get("sensorId") or item.get("sensor_id")
    if sensor:
        ward["sensorId"] = str(sensor)
    return ward


def _normalize_pin(item: object) -> dict | None:
    if not isinstance(item, dict):
        return None
    try:
        x = float(item.get("x"))
        y = float(item.get("y"))
        yaw = float(item.get("yaw") or 0.0)
    except (TypeError, ValueError):
        return None
    drag_px = item.get("dragPx")
    try:
        drag_px_n = float(drag_px) if drag_px is not None else 0.0
    except (TypeError, ValueError):
        drag_px_n = 0.0
    return {"x": x, "y": y, "yaw": yaw, "dragPx": drag_px_n}


def _load_ui_state() -> dict:
    state = default_ui_state()
    try:
        raw = json.loads(UI_STATE_PATH.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            state.update({key: raw[key] for key in state if key in raw})
    except (OSError, json.JSONDecodeError, TypeError):
        pass
    wards = [_normalize_ward(item) for item in (state.get("wards") or [])]
    state["wards"] = [item for item in wards if item][:MAX_UI_WARDS]
    state["dropPin"] = _normalize_pin(state.get("dropPin"))
    state["navGoal"] = _normalize_pin(state.get("navGoal"))
    state["isNavigating"] = bool(state.get("isNavigating"))
    status = str(state.get("navStatus") or "").strip().upper() or None
    state["navStatus"] = status if status in ALLOWED_NAV_STATUS else None
    goal_status = str(state.get("goalStatus") or "").strip().lower() or None
    state["goalStatus"] = goal_status[:32] if goal_status else None
    state["xyArrived"] = bool(state.get("xyArrived"))
    loc_status = str(state.get("locStatus") or "").strip().lower() or None
    state["locStatus"] = loc_status if loc_status in LOC_STATUS_VALUES else None
    phase = str(state.get("missionPhase") or "").strip().lower() or None
    state["missionPhase"] = phase if phase in MISSION_PHASES else None
    state["missionReady"] = (
        state.get("missionReady") if isinstance(state.get("missionReady"), bool) else None
    )
    state["missionAligned"] = (
        state.get("missionAligned")
        if isinstance(state.get("missionAligned"), bool)
        else None
    )
    state["manualControl"] = bool(state.get("manualControl"))
    try:
        state["arrivalId"] = max(0, int(state.get("arrivalId") or 0))
    except (TypeError, ValueError):
        state["arrivalId"] = 0
    last_event = str(state.get("lastEvent") or "").strip() or None
    state["lastEvent"] = last_event[:180] if last_event else None
    loc_text = str(state.get("localizationText") or "").strip() or None
    state["localizationText"] = loc_text[:180] if loc_text else None
    goal_pose = state.get("goalPose")
    if not isinstance(goal_pose, dict):
        state["goalPose"] = None
    else:
        try:
            state["goalPose"] = {
                "frame_id": str(goal_pose.get("frame_id") or FRAME_ID),
                "x": round(float(goal_pose.get("x")), 3),
                "y": round(float(goal_pose.get("y")), 3),
                "yaw": round(float(goal_pose.get("yaw") or 0.0), 3),
            }
        except (TypeError, ValueError):
            state["goalPose"] = None
    state["mode"] = "move" if state.get("mode") == "move" else "select"
    state["poseEstimating"] = bool(state.get("poseEstimating"))
    state["scentMission"] = bool(state.get("scentMission"))
    state["fragranceSpraying"] = bool(state.get("fragranceSpraying"))
    ward_id = state.get("fragranceWardId")
    state["fragranceWardId"] = str(ward_id) if ward_id else None
    state["rev"] = int(state.get("rev") or 0)
    return state


def _save_ui_state(state: dict) -> None:
    UI_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = UI_STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(UI_STATE_PATH)


_ui_lock = threading.Lock()
_ui_state = _load_ui_state()


def snapshot_ui() -> dict:
    with _ui_lock:
        snap = json.loads(json.dumps(_ui_state))
    for ward in snap.get("wards") or []:
        if isinstance(ward, dict):
            ward["name"] = _display_ward_name(ward.get("id"), ward.get("name"))
    return snap


def merge_ui(patch: dict | None) -> dict:
    if not isinstance(patch, dict):
        return snapshot_ui()

    with _ui_lock:
        if "wards" in patch:
            wards = [_normalize_ward(item) for item in (patch.get("wards") or [])]
            _ui_state["wards"] = [item for item in wards if item][:MAX_UI_WARDS]
        if "selectedId" in patch:
            selected = patch.get("selectedId")
            _ui_state["selectedId"] = str(selected) if selected else None
        if "dropPin" in patch:
            _ui_state["dropPin"] = _normalize_pin(patch.get("dropPin"))
        if "navGoal" in patch:
            _ui_state["navGoal"] = _normalize_pin(patch.get("navGoal"))
        if "isNavigating" in patch:
            _ui_state["isNavigating"] = bool(patch.get("isNavigating"))
        if "navStatus" in patch:
            status = str(patch.get("navStatus") or "").strip().upper() or None
            _ui_state["navStatus"] = (
                status if status in ALLOWED_NAV_STATUS else None
            )
        if "goalStatus" in patch:
            goal_status = str(patch.get("goalStatus") or "").strip().lower() or None
            _ui_state["goalStatus"] = goal_status[:32] if goal_status else None
        if "xyArrived" in patch:
            _ui_state["xyArrived"] = bool(patch.get("xyArrived"))
        if "locStatus" in patch:
            loc_status = str(patch.get("locStatus") or "").strip().lower() or None
            _ui_state["locStatus"] = (
                loc_status if loc_status in LOC_STATUS_VALUES else None
            )
        if "missionPhase" in patch:
            phase = str(patch.get("missionPhase") or "").strip().lower() or None
            _ui_state["missionPhase"] = phase if phase in MISSION_PHASES else None
        if "missionReady" in patch:
            ready = patch.get("missionReady")
            _ui_state["missionReady"] = ready if isinstance(ready, bool) else None
        if "missionAligned" in patch:
            aligned = patch.get("missionAligned")
            _ui_state["missionAligned"] = aligned if isinstance(aligned, bool) else None
        if "manualControl" in patch:
            _ui_state["manualControl"] = bool(patch.get("manualControl"))
        if "arrivalId" in patch:
            try:
                _ui_state["arrivalId"] = max(0, int(patch.get("arrivalId") or 0))
            except (TypeError, ValueError):
                _ui_state["arrivalId"] = 0
        if "lastEvent" in patch:
            text = str(patch.get("lastEvent") or "").strip()
            _ui_state["lastEvent"] = text[:180] or None
        if "localizationText" in patch:
            text = str(patch.get("localizationText") or "").strip()
            _ui_state["localizationText"] = text[:180] or None
        if "goalPose" in patch:
            goal_pose = patch.get("goalPose")
            if not isinstance(goal_pose, dict):
                _ui_state["goalPose"] = None
            else:
                try:
                    _ui_state["goalPose"] = {
                        "frame_id": str(goal_pose.get("frame_id") or FRAME_ID),
                        "x": round(float(goal_pose.get("x")), 3),
                        "y": round(float(goal_pose.get("y")), 3),
                        "yaw": round(float(goal_pose.get("yaw") or 0.0), 3),
                    }
                except (TypeError, ValueError):
                    _ui_state["goalPose"] = None
        if "arrivalSequence" in patch:
            seq = patch.get("arrivalSequence")
            _ui_state["arrivalSequence"] = seq if isinstance(seq, dict) else None
        if "mode" in patch:
            _ui_state["mode"] = "move" if patch.get("mode") == "move" else "select"
        if "poseEstimating" in patch:
            _ui_state["poseEstimating"] = bool(patch.get("poseEstimating"))
        if "scentMission" in patch:
            _ui_state["scentMission"] = bool(patch.get("scentMission"))
        if "fragranceSpraying" in patch:
            _ui_state["fragranceSpraying"] = bool(patch.get("fragranceSpraying"))
        if "fragranceWardId" in patch:
            ward_id = patch.get("fragranceWardId")
            _ui_state["fragranceWardId"] = str(ward_id) if ward_id else None

        _ui_state["rev"] = int(_ui_state.get("rev") or 0) + 1
        _ui_state["updatedAt"] = time.time()
        _save_ui_state(_ui_state)
        return json.loads(json.dumps(_ui_state))


class Handler(BaseHTTPRequestHandler):
    timeout = None
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return

    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        try:
            self.send_response(status)
            self._cors()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
            self.wfile.flush()
        except Exception as exc:  # noqa: BLE001
            print(f"[ros-nav-bridge] send_json 실패: {exc}", file=sys.stderr)

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/api/nav/pose":
            self._send_json(200, get_amcl_pose())
            return

        if path == "/api/nav/ui":
            self._send_json(200, {"ok": True, "state": snapshot_ui()})
            return

        if path not in ("/api/nav/health", "/api/nav/status"):
            self._send_json(404, {"ok": False})
            return

        ready = ensure_ros()
        pose = get_amcl_pose()
        ui = snapshot_ui()
        self._send_json(
            200,
            {
                "ok": True,
                "ready": ready,
                "go_to_goal_topic": f"/{GO_TO_GOAL_TOPIC}",
                "go_around_topic": f"/{GO_AROUND_TOPIC}",
                "save_goal_topic": f"/{SAVE_GOAL_TOPIC}",
                "save_boundary_topic": f"/{SAVE_BOUNDARY_TOPIC}",
                "save_region_topic": f"/{SAVE_REGION_TOPIC}",
                "arrival_topic": f"/{ARRIVAL_TOPIC}",
                "status_topic": f"/{IMPACT_STATUS_TOPIC}",
                "events_topic": f"/{IMPACT_EVENTS_TOPIC}",
                "localization_topic": f"/{LOCALIZATION_TOPIC}",
                "manual_topic": f"/{MANUAL_TOPIC}",
                "goal_pose_topic": f"/{IMPACT_GOAL_TOPIC}",
                "initial_topic": f"/{INITIAL_TOPIC}",
                "amcl_topic": f"/{AMCL_TOPIC}",
                "frame_id": FRAME_ID,
                "goal_published": _goal_count,
                "initial_published": _initial_count,
                "cancel_published": _cancel_count,
                "commands": dict(_command_counts),
                "mission_phase": ui.get("missionPhase"),
                "mission_ready": ui.get("missionReady"),
                "loc_status": ui.get("locStatus"),
                "manual_control": ui.get("manualControl"),
                "amcl_live": bool(pose.get("live")),
                "error": _last_error,
            },
        )

    def do_PUT(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path != "/api/nav/ui":
            self._send_json(404, {"ok": False})
            return

        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8") if raw else "{}")
            if not isinstance(body, dict):
                raise ValueError("state must be an object")
        except (json.JSONDecodeError, ValueError) as exc:
            self._send_json(400, {"ok": False, "error": str(exc)})
            return

        patch = body.get("state", body)
        self._send_json(200, {"ok": True, "state": merge_ui(patch)})

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/api/nav/cancel":
            length = int(self.headers.get("Content-Length", "0"))
            if length:
                self.rfile.read(length)
            try:
                result = cancel_navigation()
            except Exception as exc:  # noqa: BLE001
                print(f"[ros-nav-bridge] cancel handler: {exc}", file=sys.stderr)
                result = {"ok": False, "error": str(exc)}
            self._send_json(200 if result.get("ok") else 503, result)
            return

        if path in ("/api/nav/diffusion_complete", "/api/nav/go_around"):
            length = int(self.headers.get("Content-Length", "0"))
            if length:
                self.rfile.read(length)
            result = publish_diffusion_complete()
            self._send_json(200 if result.get("ok") else 503, result)
            return

        if path in ("/api/nav/arrival_sequence/test", "/api/nav/arrival_sequence/stop"):
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length else b"{}"
            try:
                body = json.loads(raw.decode("utf-8") if raw else "{}") or {}
            except (json.JSONDecodeError, TypeError, ValueError):
                body = {}
            seq = _get_sequence()
            if path.endswith("/test"):
                result = seq.start(body.get("purify_sec"), body.get("spray_sec"), reason="test",
                                   go_around=bool(body.get("go_around", False)))
            else:
                result = seq.stop()
            self._send_json(200 if result.get("ok") else 409, result)
            return

        if path == "/api/nav/command":
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length else b"{}"
            try:
                body = json.loads(raw.decode("utf-8") if raw else "{}")
                command = str(body.get("command") or body.get("name") or "")
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                self._send_json(400, {"ok": False, "error": str(exc)})
                return
            result = publish_empty_command(command)
            self._send_json(200 if result.get("ok") else 400, result)
            return

        if path not in ("/api/nav/goal", "/api/nav/initialpose"):
            self._send_json(404, {"ok": False})
            return

        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"

        try:
            x, y, yaw, frame_id = _parse_pose_body(raw)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            self._send_json(400, {"ok": False, "error": f"invalid body: {exc}"})
            return

        try:
            if path == "/api/nav/goal":
                sent = publish_goal(x, y, yaw, frame_id)
                topic = f"/{GO_TO_GOAL_TOPIC}"
            else:
                sent = publish_initial_pose(x, y, yaw, frame_id)
                topic = f"/{INITIAL_TOPIC}"
            self._send_json(
                200 if sent else 503,
                {
                    "ok": sent,
                    "sent": sent,
                    "x": x,
                    "y": y,
                    "yaw": yaw,
                    "frame_id": frame_id,
                    "topic": topic,
                    "error": None if sent else _last_error,
                },
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[ros-nav-bridge] {path} 실패: {exc}", file=sys.stderr)
            self._send_json(
                200,
                {
                    "ok": False,
                    "error": str(exc) or "initialpose publish failed",
                    "topic": path,
                },
            )


class Server(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False
    allow_reuse_address = True


_sequence: ArrivalSequence | None = None


def _go_around_after_sequence() -> dict:
    """도착 후 청정·발향이 끝나면 정찰 재개 (publish_diffusion_complete 와 같지만 순서를 멈추지 않음)."""
    result = publish_empty_command("go_around")
    if result.get("ok"):
        merge_ui({"isNavigating": False, "navStatus": None, "scentMission": False, "missionPhase": "patrol"})
    return result


def _get_sequence() -> ArrivalSequence:
    global _sequence
    if _sequence is None:
        _sequence = ArrivalSequence(merge_ui, _go_around_after_sequence)
    return _sequence


def main() -> None:
    server = Server(("127.0.0.1", PORT), Handler)
    print(
        f"[ros-nav-bridge] http://127.0.0.1:{PORT} "
        f"→ /{GO_TO_GOAL_TOPIC} /{GO_AROUND_TOPIC} /{INITIAL_TOPIC}, "
        f"← /{ARRIVAL_TOPIC} /{IMPACT_STATUS_TOPIC} /{AMCL_TOPIC}",
        file=sys.stderr,
    )
    _import_ros()
    threading.Thread(target=ensure_ros, daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        try:
            if rclpy is not None and rclpy.ok() and _node is not None:
                _node.destroy_node()
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    main()
