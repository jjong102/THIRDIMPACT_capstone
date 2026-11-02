#!/usr/bin/env python3
"""HTTP ↔ ROS 2 Nav2: /goal_pose, /initialpose, /amcl_pose, cancel/stop."""

from __future__ import annotations

import json
import math
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORT = int(os.environ.get("ROS_NAV_BRIDGE_PORT", "5179"))
GOAL_TOPIC = os.environ.get("ROS_GOAL_TOPIC", "goal_pose")
INITIAL_TOPIC = os.environ.get("ROS_INITIAL_POSE_TOPIC", "initialpose")
AMCL_TOPIC = os.environ.get("ROS_AMCL_POSE_TOPIC", "amcl_pose")
CMD_VEL_TOPIC = os.environ.get("ROS_CMD_VEL_TOPIC", "cmd_vel")
NAV_CANCEL_ACTIONS = [
    name.strip()
    for name in os.environ.get(
        "ROS_NAV_CANCEL_ACTIONS",
        "navigate_to_pose,navigate_through_poses,bt_navigator/navigate_to_pose",
    ).split(",")
    if name.strip()
]
FRAME_ID = os.environ.get("ROS_GOAL_FRAME_ID", "map")
DIFFUSION_COMPLETE_TOPIC = os.environ.get(
    "ROS_DIFFUSION_COMPLETE_TOPIC", "diffusion_complete"
)
GOAL_STATUS_TOPIC = os.environ.get("ROS_GOAL_STATUS_TOPIC", "goal_status")
LOC_STATUS_TOPIC = os.environ.get("ROS_LOC_STATUS_TOPIC", "loc_status")
NAV_STATUS_ACTIONS = NAV_CANCEL_ACTIONS
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
Twist = None
CancelGoal = None
Empty = None
String = None
GoalStatusArray = None
QoSProfile = None
ReliabilityPolicy = None
HistoryPolicy = None
DurabilityPolicy = None

_node = None
_goal_pub = None
_initial_pub = None
_cmd_pub = None
_amcl_sub = None
_diffusion_pub = None
_goal_status_sub = None
_loc_status_sub = None
_status_subs: list = []
_last_loc_status: str | None = None
_ignore_goal_status_until = 0.0
_cancel_clients: dict = {}
_interfaces_ready = False
_goal_checker_tuned = False
_param_client = None
_lock = threading.Lock()
_init_lock = threading.Lock()
_spin_thread = None
_last_error: str | None = None
_goal_count = 0
_initial_count = 0
_cancel_count = 0
_diffusion_count = 0
_amcl_pose: dict | None = None
_nav_result: dict = {
    "status": None,
    "stamp": None,
}
_status_primed = False
_seen_terminal_goals: set[bytes] = set()
_last_emitted_nav: tuple | None = None

STATUS_EXECUTING = 2
STATUS_SUCCEEDED = 4
STATUS_CANCELED = 5
STATUS_ABORTED = 6
ALLOWED_NAV_STATUS = {
    "EXECUTING",
    "XY_ARRIVED",
    "SUCCEEDED",
    "CANCELED",
    "ABORTED",
}
GOAL_STATUS_MAP = {
    "xy_arrived": ("XY_ARRIVED", True),
    "succeeded": ("SUCCEEDED", False),
    "executing": ("EXECUTING", True),
    "canceled": ("CANCELED", False),
    "aborted": ("ABORTED", False),
}
LOC_STATUS_VALUES = {"not_localized", "localized"}


def _import_ros() -> bool:
    global rclpy, PoseStamped, PoseWithCovarianceStamped, Twist, CancelGoal
    global Empty, String, GoalStatusArray
    global QoSProfile, ReliabilityPolicy, HistoryPolicy, DurabilityPolicy
    try:
        import rclpy as _rclpy
        from action_msgs.msg import GoalStatusArray as _GoalStatusArray
        from action_msgs.srv import CancelGoal as _CancelGoal
        from geometry_msgs.msg import PoseStamped as _PoseStamped
        from geometry_msgs.msg import PoseWithCovarianceStamped as _PoseCov
        from geometry_msgs.msg import Twist as _Twist
        from rclpy.qos import DurabilityPolicy as _DurabilityPolicy
        from rclpy.qos import HistoryPolicy as _HistoryPolicy
        from rclpy.qos import QoSProfile as _QoSProfile
        from rclpy.qos import ReliabilityPolicy as _ReliabilityPolicy
        from std_msgs.msg import Empty as _Empty
        from std_msgs.msg import String as _String

        rclpy = _rclpy
        PoseStamped = _PoseStamped
        PoseWithCovarianceStamped = _PoseCov
        Twist = _Twist
        CancelGoal = _CancelGoal
        Empty = _Empty
        String = _String
        GoalStatusArray = _GoalStatusArray
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


def _goal_id_bytes(status) -> bytes:
    try:
        return bytes(status.goal_info.goal_id.uuid)
    except Exception:  # noqa: BLE001
        return b""


def _emit_nav_status(
    status_name: str, navigating: bool, extra: dict | None = None
) -> None:
    global _last_emitted_nav, _nav_result
    key = (status_name, navigating)
    if key == _last_emitted_nav and not extra:
        return
    _last_emitted_nav = key
    _nav_result = {"status": status_name, "stamp": time.time()}
    patch = {"isNavigating": navigating, "navStatus": status_name}
    if extra:
        patch.update(extra)
    if status_name in {"EXECUTING", "SUCCEEDED", "CANCELED", "ABORTED"}:
        patch.setdefault("xyArrived", False)
    if status_name == "XY_ARRIVED":
        patch.setdefault("xyArrived", True)
    merge_ui(patch)
    print(f"[ros-nav-bridge] nav {status_name}", file=sys.stderr)


def _on_loc_status(msg) -> None:
    global _last_loc_status
    data = str(getattr(msg, "data", "") or "").strip().lower()
    if data not in LOC_STATUS_VALUES:
        return
    if data == _last_loc_status:
        return
    _last_loc_status = data
    merge_ui({"locStatus": data})
    print(f"[ros-nav-bridge] loc {data}", file=sys.stderr)


def _on_goal_status(msg) -> None:
    data = str(getattr(msg, "data", "") or "").strip().lower()
    mapped = GOAL_STATUS_MAP.get(data)
    if mapped is None:
        return
    # 사용자가 방금 정지했으면, 다른 Jetson의 늦은 executing 이 다시 달리게 두지 않음
    if data in {"executing", "xy_arrived", "succeeded"} and time.time() < _ignore_goal_status_until:
        return
    status_name, navigating = mapped
    extra = {"goalStatus": data}
    if data == "xy_arrived":
        extra["xyArrived"] = True
    elif data in {"executing", "succeeded", "canceled", "aborted"}:
        extra["xyArrived"] = False
    _emit_nav_status(status_name, navigating, extra)


def _on_nav_status(msg) -> None:
    global _status_primed, _seen_terminal_goals

    statuses = list(getattr(msg, "status_list", []) or [])
    with _lock:
        if not _status_primed:
            for item in statuses:
                code = int(getattr(item, "status", 0) or 0)
                if code in (STATUS_SUCCEEDED, STATUS_CANCELED, STATUS_ABORTED):
                    gid = _goal_id_bytes(item)
                    if gid:
                        _seen_terminal_goals.add(gid)
            _status_primed = True
            return

        newest_terminal = None
        for item in statuses:
            code = int(getattr(item, "status", 0) or 0)
            gid = _goal_id_bytes(item)
            if not gid:
                continue
            if code in (STATUS_SUCCEEDED, STATUS_CANCELED, STATUS_ABORTED):
                if gid in _seen_terminal_goals:
                    continue
                _seen_terminal_goals.add(gid)
                if len(_seen_terminal_goals) > 64:
                    _seen_terminal_goals.clear()
                    _seen_terminal_goals.add(gid)
                newest_terminal = code

    if newest_terminal == STATUS_SUCCEEDED:
        _emit_nav_status("SUCCEEDED", False)
    elif newest_terminal == STATUS_CANCELED:
        _emit_nav_status("CANCELED", False)
    elif newest_terminal == STATUS_ABORTED:
        _emit_nav_status("ABORTED", False)


def _topic_name(name: str) -> str:
    return name if str(name).startswith("/") else f"/{name}"


def _ensure_stop_interfaces() -> None:
    global _cmd_pub, _cancel_clients, _diffusion_pub, _goal_status_sub
    global _loc_status_sub, _status_subs, _interfaces_ready

    if _node is None or _interfaces_ready:
        return

    if Twist is not None and _cmd_pub is None:
        _cmd_pub = _node.create_publisher(Twist, CMD_VEL_TOPIC, 10)

    if Empty is not None and _diffusion_pub is None:
        _diffusion_pub = _node.create_publisher(Empty, DIFFUSION_COMPLETE_TOPIC, 10)

    if CancelGoal is not None:
        for action_name in NAV_CANCEL_ACTIONS:
            if action_name in _cancel_clients:
                continue
            _cancel_clients[action_name] = _node.create_client(
                CancelGoal,
                f"{action_name}/_action/cancel_goal",
            )

    if GoalStatusArray is not None and QoSProfile is not None:
        known = {
            _topic_name(getattr(sub, "topic_name", "") or "")
            for sub in _status_subs
        }
        status_qos = QoSProfile(
            depth=10,
            reliability=ReliabilityPolicy.RELIABLE,
            history=HistoryPolicy.KEEP_LAST,
        )
        for action_name in NAV_STATUS_ACTIONS:
            topic = _topic_name(f"{action_name}/_action/status")
            if topic in known:
                continue
            _status_subs.append(
                _node.create_subscription(
                    GoalStatusArray,
                    topic,
                    _on_nav_status,
                    status_qos,
                )
            )
            known.add(topic)

    if String is not None and QoSProfile is not None and _goal_status_sub is None:
        goal_status_qos = QoSProfile(
            depth=10,
            reliability=ReliabilityPolicy.RELIABLE,
            history=HistoryPolicy.KEEP_LAST,
        )
        _goal_status_sub = _node.create_subscription(
            String,
            _topic_name(GOAL_STATUS_TOPIC),
            _on_goal_status,
            goal_status_qos,
        )
        print(
            f"[ros-nav-bridge] ← /{GOAL_STATUS_TOPIC} (std_msgs/String)",
            file=sys.stderr,
        )

    if String is not None and QoSProfile is not None and _loc_status_sub is None:
        loc_status_qos = QoSProfile(
            depth=10,
            reliability=ReliabilityPolicy.RELIABLE,
            history=HistoryPolicy.KEEP_LAST,
        )
        _loc_status_sub = _node.create_subscription(
            String,
            _topic_name(LOC_STATUS_TOPIC),
            _on_loc_status,
            loc_status_qos,
        )
        print(
            f"[ros-nav-bridge] ← /{LOC_STATUS_TOPIC} (std_msgs/String)",
            file=sys.stderr,
        )

    _interfaces_ready = True


def _tune_controller_goal_checker() -> None:
    """도착 후 제자리 회전으로 progress checker가 실패하지 않게 yaw 허용을 조금 넓힌다."""
    global _goal_checker_tuned, _param_client

    if _goal_checker_tuned or _node is None:
        return

    try:
        from rcl_interfaces.msg import Parameter, ParameterType, ParameterValue
        from rcl_interfaces.srv import SetParameters
    except Exception as exc:  # noqa: BLE001
        print(f"[ros-nav-bridge] yaw tune skip: {exc}", file=sys.stderr)
        _goal_checker_tuned = True
        return

    if _param_client is None:
        _param_client = _node.create_client(
            SetParameters, "/controller_server/set_parameters"
        )
    if not _param_client.service_is_ready():
        return

    yaw = Parameter()
    yaw.name = "goal_checker.yaw_goal_tolerance"
    yaw.value = ParameterValue(
        type=ParameterType.PARAMETER_DOUBLE,
        double_value=0.45,
    )
    req = SetParameters.Request()
    req.parameters = [yaw]
    _param_client.call_async(req)
    _goal_checker_tuned = True
    print(
        "[ros-nav-bridge] yaw_goal_tolerance → 0.45 rad (제자리 회전 타임아웃 완화)",
        file=sys.stderr,
    )


def _ros_handles_alive() -> bool:
    return (
        rclpy is not None
        and rclpy.ok()
        and _node is not None
        and _goal_pub is not None
        and _initial_pub is not None
        and _amcl_sub is not None
    )


def _reset_ros_handles() -> None:
    global _node, _goal_pub, _initial_pub, _amcl_sub, _cmd_pub
    global _diffusion_pub, _goal_status_sub, _loc_status_sub, _status_subs
    global _cancel_clients, _interfaces_ready, _param_client, _goal_checker_tuned
    _node = None
    _goal_pub = None
    _initial_pub = None
    _cmd_pub = None
    _amcl_sub = None
    _diffusion_pub = None
    _goal_status_sub = None
    _loc_status_sub = None
    _status_subs = []
    _cancel_clients = {}
    _interfaces_ready = False
    _param_client = None
    _goal_checker_tuned = False


def ensure_ros() -> bool:
    global _node, _goal_pub, _initial_pub, _amcl_sub, _spin_thread, _last_error
    global _cmd_pub, _cancel_clients, _diffusion_pub, _goal_status_sub
    global _loc_status_sub, _status_subs, _interfaces_ready

    if _ros_handles_alive():
        return True

    if _initial_pub is not None or _goal_pub is not None:
        print("[ros-nav-bridge] ROS 컨텍스트가 끊겨 다시 붙입니다", file=sys.stderr)
        _reset_ros_handles()

    if rclpy is None and not _import_ros():
        return False

    assert rclpy is not None
    assert PoseStamped is not None
    assert PoseWithCovarianceStamped is not None
    assert QoSProfile is not None
    assert DurabilityPolicy is not None

    with _init_lock:
        if _goal_pub is not None and _initial_pub is not None and _amcl_sub is not None:
            return True

        try:
            if not rclpy.ok():
                rclpy.init(args=None)

            _node = rclpy.create_node("dashboard_nav_bridge")
            pub_qos = QoSProfile(
                depth=1,
                reliability=ReliabilityPolicy.RELIABLE,
                history=HistoryPolicy.KEEP_LAST,
            )
            _goal_pub = _node.create_publisher(PoseStamped, GOAL_TOPIC, pub_qos)
            _initial_pub = _node.create_publisher(
                PoseWithCovarianceStamped, INITIAL_TOPIC, pub_qos
            )
            _ensure_stop_interfaces()

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
            _node.create_timer(2.0, _tune_controller_goal_checker)

            def _spin() -> None:
                try:
                    rclpy.spin(_node)
                except Exception:  # noqa: BLE001
                    pass

            _spin_thread = threading.Thread(target=_spin, daemon=True)
            _spin_thread.start()
            _last_error = None
            print(
                f"[ros-nav-bridge] → /{GOAL_TOPIC} + /{INITIAL_TOPIC}, "
                f"cmd={CMD_VEL_TOPIC}, cancel={','.join(NAV_CANCEL_ACTIONS)}, "
                f"complete=/{DIFFUSION_COMPLETE_TOPIC}, "
                f"← /{GOAL_STATUS_TOPIC} + /{LOC_STATUS_TOPIC} + /{AMCL_TOPIC} (frame={FRAME_ID})",
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


def publish_goal(x: float, y: float, yaw: float = 0.0, frame_id: str = FRAME_ID) -> bool:
    global _goal_count, _last_error, _ignore_goal_status_until

    if not ensure_ros():
        return False

    assert _node is not None
    assert _goal_pub is not None
    assert PoseStamped is not None

    msg = PoseStamped()
    msg.header.stamp = _node.get_clock().now().to_msg()
    msg.header.frame_id = frame_id or FRAME_ID
    msg.pose.position.x = float(x)
    msg.pose.position.y = float(y)
    msg.pose.position.z = 0.0
    qx, qy, qz, qw = yaw_to_quaternion(float(yaw))
    msg.pose.orientation.x = qx
    msg.pose.orientation.y = qy
    msg.pose.orientation.z = qz
    msg.pose.orientation.w = qw

    try:
        _goal_pub.publish(msg)
    except Exception as exc:  # noqa: BLE001
        if "context is invalid" not in str(exc).lower():
            _last_error = str(exc)
            raise
        print("[ros-nav-bridge] goal 컨텍스트 끊김, 재연결", file=sys.stderr)
        _reset_ros_handles()
        if not ensure_ros():
            _last_error = str(exc)
            return False
        assert _goal_pub is not None
        _goal_pub.publish(msg)
    with _lock:
        _goal_count += 1
        _ignore_goal_status_until = 0.0

    print(
        f"[ros-nav-bridge] → /{GOAL_TOPIC} x={x:.3f} y={y:.3f} yaw={yaw:.3f}",
        file=sys.stderr,
    )
    _last_error = None
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


def _publish_zero_cmd() -> None:
    if _cmd_pub is None or Twist is None:
        return
    _cmd_pub.publish(Twist())


def _wait_future(future, timeout_sec: float) -> bool:
    deadline = time.time() + timeout_sec
    while not future.done() and time.time() < deadline:
        time.sleep(0.02)
    return future.done()


def _cancel_nav_actions() -> None:
    for action_name, client in list(_cancel_clients.items()):
        try:
            future = client.call_async(CancelGoal.Request())
            _wait_future(future, 0.8)
        except Exception as exc:  # noqa: BLE001
            print(
                f"[ros-nav-bridge] cancel {action_name} failed: {exc}",
                file=sys.stderr,
            )


def cancel_navigation() -> dict:
    global _cancel_count, _last_error, _ignore_goal_status_until

    ensure_ros()
    _ensure_stop_interfaces()

    with _lock:
        _cancel_count += 1
        _ignore_goal_status_until = time.time() + 4.0

    for _ in range(6):
        _publish_zero_cmd()

    threading.Thread(target=_cancel_nav_actions, daemon=True).start()

    print(
        f"[ros-nav-bridge] stop cmd_vel=/{CMD_VEL_TOPIC}",
        file=sys.stderr,
    )
    _last_error = None
    return {
        "ok": True,
        "cmd_vel_topic": f"/{CMD_VEL_TOPIC}",
        "cancelled": list(_cancel_clients.keys()),
    }


def publish_diffusion_complete() -> dict:
    global _diffusion_count, _last_error

    if not ensure_ros():
        return {"ok": False, "error": _last_error or "ros not ready"}

    if _diffusion_pub is None or Empty is None:
        return {"ok": False, "error": "diffusion publisher missing"}

    _diffusion_pub.publish(Empty())
    with _lock:
        _diffusion_count += 1
        count = _diffusion_count

    print(
        f"[ros-nav-bridge] → /{DIFFUSION_COMPLETE_TOPIC} Empty ({count})",
        file=sys.stderr,
    )
    _last_error = None
    return {
        "ok": True,
        "topic": f"/{DIFFUSION_COMPLETE_TOPIC}",
        "type": "std_msgs/msg/Empty",
        "published": count,
    }


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
    state["goalStatus"] = goal_status if goal_status in GOAL_STATUS_MAP else None
    state["xyArrived"] = bool(state.get("xyArrived"))
    loc_status = str(state.get("locStatus") or "").strip().lower() or None
    state["locStatus"] = loc_status if loc_status in LOC_STATUS_VALUES else None
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
            _ui_state["goalStatus"] = (
                goal_status if goal_status in GOAL_STATUS_MAP else None
            )
        if "xyArrived" in patch:
            _ui_state["xyArrived"] = bool(patch.get("xyArrived"))
        if "locStatus" in patch:
            loc_status = str(patch.get("locStatus") or "").strip().lower() or None
            _ui_state["locStatus"] = (
                loc_status if loc_status in LOC_STATUS_VALUES else None
            )
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
        self._send_json(
            200,
            {
                "ok": True,
                "ready": ready,
                "goal_topic": f"/{GOAL_TOPIC}",
                "initial_topic": f"/{INITIAL_TOPIC}",
                "amcl_topic": f"/{AMCL_TOPIC}",
                "cmd_vel_topic": f"/{CMD_VEL_TOPIC}",
                "frame_id": FRAME_ID,
                "goal_published": _goal_count,
                "initial_published": _initial_count,
                "cancel_published": _cancel_count,
                "diffusion_published": _diffusion_count,
                "diffusion_topic": f"/{DIFFUSION_COMPLETE_TOPIC}",
                "goal_status_topic": f"/{GOAL_STATUS_TOPIC}",
                "loc_status_topic": f"/{LOC_STATUS_TOPIC}",
                "loc_status": _last_loc_status,
                "nav_status": _nav_result.get("status"),
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
                _emit_nav_status("CANCELED", False)
            except Exception as exc:  # noqa: BLE001
                print(f"[ros-nav-bridge] cancel handler: {exc}", file=sys.stderr)
                result = {"ok": True, "error": str(exc)}
                try:
                    _emit_nav_status("CANCELED", False)
                except Exception:  # noqa: BLE001
                    pass
            self._send_json(200, result)
            return

        if path == "/api/nav/diffusion_complete":
            length = int(self.headers.get("Content-Length", "0"))
            if length:
                self.rfile.read(length)
            result = publish_diffusion_complete()
            self._send_json(200 if result.get("ok") else 503, result)
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
                topic = f"/{GOAL_TOPIC}"
                if sent:
                    _emit_nav_status("EXECUTING", True)
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


def main() -> None:
    server = Server(("127.0.0.1", PORT), Handler)
    print(
        f"[ros-nav-bridge] http://127.0.0.1:{PORT} "
        f"→ /{GOAL_TOPIC} + /{INITIAL_TOPIC}, ← /{AMCL_TOPIC}",
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
