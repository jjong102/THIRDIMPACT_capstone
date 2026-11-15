"""Single-owner navigation supervisor. Runs without RViz or debug processes."""
import inspect
import json
import copy
import fcntl
import math
import os
from pathlib import Path
from ament_index_python.packages import get_package_share_directory
import random
import time

import numpy as np
from scipy.ndimage import distance_transform_edt
import rclpy
from rclpy.action import ActionClient
from rclpy.node import Node
from rclpy.qos import QoSProfile, DurabilityPolicy, ReliabilityPolicy, qos_profile_sensor_data
from rclpy.time import Time
from action_msgs.msg import GoalStatus
from geometry_msgs.msg import Twist, PoseStamped, PoseWithCovarianceStamped
from nav_msgs.msg import OccupancyGrid, Odometry, Path as NavPath
from nav2_msgs.action import NavigateToPose, ComputePathToPose
from nav2_msgs.msg import Costmap
from sensor_msgs.msg import LaserScan
from std_msgs.msg import Empty, Bool, String
from visualization_msgs.msg import Marker, MarkerArray
from tf2_ros import Buffer, TransformListener, TransformException

from .core import (wrap, relative, transform, voxel, register_scan, alignment_command,
                   swept_clear, inside_patrol_region, outside_exclusion, travel_allowed, region_map, map_fingerprint, atomic_save,
                   accepts_return, accepts_patrol)
from .editor import RectangleEditor, orientation, yaw
from .scan_pairs import ScanPairs
from .amcl_scan import apply_transform
from .progress_watch import ProgressWatch
from .pose_tracking import PoseBridge
from .return_control import ReturnControl
from .patrol_control import PatrolControl
from .patrol_planning import PatrolPlanning, costmap_component



class _ZoneRect:
    def __init__(self, rect):
        self.rect = list(rect)


class ImpactNode(PatrolPlanning, PatrolControl, ReturnControl, Node):
    def __init__(self):
        super().__init__('third_impact')
        defaults = dict(mode='mission', map_file='', state_file='~/.ros/third_impact/goal.json',
                        xy_tolerance=.02, yaw_tolerance_deg=5., settle_seconds=1.,
                        capture_seconds=2., align_radius=.4, scan_timeout=.45,
                        patrol_tolerance=.05,
                        return_max_speed=.4, return_max_angular_speed=.35, return_obstacle_margin=.01,
                        return_linear_accel=.35, return_angular_accel=.4,
                        require_localization_ready=True,
                        patrol_bt_file=str(Path(get_package_share_directory('third_impact'))/'config/patrol.xml'),
                        return_bt_file=str(Path(get_package_share_directory('third_impact'))/'config/return.xml'),
                        pose_timeout=1., odom_timeout=.3, stopped_linear=.005,
                        stopped_angular=.01, max_align_v=.035, max_align_w=.12,
                        match_rmse=.025, match_overlap=.65, match_observability=.015,
                        match_max_condition=500., match_max_correction=.20,
                        match_max_angle_deg=15., retry_seconds=2., patrol_clearance=.45,
                        amcl_max_xy_variance=.08, amcl_max_yaw_variance=.08,
                        footprint_half_length=.35, footprint_half_width=.22,
                        obstacle_margin=.04, scan_max_range=6.,
                        front_scan='/rplidar1/scan_filtered', rear_scan='/rplidar2/scan_filtered')
        self.p = {k: self.declare_parameter(k, v).value for k, v in defaults.items()}
        self.mode = self.p['mode']
        if self.mode not in ('mission', 'teach'):
            raise ValueError('mode must be mission or teach')
        self.path = Path(self.p['state_file']).expanduser()
        ros_home = Path(os.environ.get('ROS_HOME', str(Path.home()/'.ros')))
        lock_path = ros_home/('third_impact_domain_'+os.environ.get('ROS_DOMAIN_ID', '0')+'.lock')
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        self.owner_lock = lock_path.open('a')
        try:
            fcntl.flock(self.owner_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError('Another third_impact teaching/mission node is already running') from exc
        self.fingerprint = map_fingerprint(self.p['map_file'])
        self.data = None
        self.detail = 'Waiting for map, localization, sensors and saved goal/rectangle'
        self.state = 'WAIT' if self.mode == 'mission' else 'TEACH'
        self.intent = 'patrol'
        self.manual = None; self.manual_stamp = 0.
        self.odom = None; self.odom_stamp = 0.
        self.amcl = None; self.amcl_stamp = 0.
        self.localization_info = {}; self.localization_stamp = 0.
        self.scan_pairs = ScanPairs()
        self.scans = {}; self.grid = None; self.candidates = None
        self.nav_handle = None; self.nav_pending = False; self.cancel_requested = False
        self.nav_purpose = None; self.nav_target = None
        self.patrol_retry_target = None
        self.patrol_reached = False; self.patrol_visits = 0
        self.last_navigation_result = None
        self.nav_velocity = Twist(); self.nav_velocity_stamp = 0.
        self.next_attempt = 0.; self.settled_since = None
        self.capture_start = 0.; self.capture_pose = None; self.capture_clouds = []
        self.capture_signature = None; self.match = None
        self.align_last_key = None; self.align_velocity = (0., 0.); self.align_valid_stamp = 0.
        # TF reception must not wait behind collision checking in the mission executor.
        self.tf_node = Node('third_impact_tf_listener', use_global_arguments=False,
                            parameter_overrides=[rclpy.parameter.Parameter(
                                'use_sim_time', value=self.get_parameter('use_sim_time').value)])
        self.tf = Buffer(); self.listener = TransformListener(self.tf, self.tf_node, spin_thread=True)
        self.pose_bridge = PoseBridge()
        self.pose_diagnostics = {}
        self._pose_snapshot_active = False
        durable = QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL,
                             reliability=ReliabilityPolicy.RELIABLE)
        self.velocity_pub = self.create_publisher(Twist, '/cmd_vel', 10)
        self.arrival_pub = self.create_publisher(Empty, '/arrival_at_goal', 10)
        self.status_pub = self.create_publisher(String, '/third_impact/status', 10)
        self.event_pub = self.create_publisher(String, '/third_impact/events', 10)
        self.marker_pub = self.create_publisher(MarkerArray, '/third_impact/markers', durable)
        self.planning_map_pub = self.create_publisher(OccupancyGrid, '/third_impact/planning_map', durable)
        self.navigation_map_pub = self.create_publisher(OccupancyGrid, '/third_impact/navigation_map', durable)
        self.goal_pub = self.create_publisher(PoseStamped, '/third_impact/goal', durable)
        self.create_subscription(OccupancyGrid, '/map', self.on_map, durable)
        self.create_subscription(PoseWithCovarianceStamped, '/amcl_pose', self.on_amcl, durable)
        self.create_subscription(String, '/third_impact/localization', self.on_localization, durable)
        latest_sensor = QoSProfile(depth=1, reliability=ReliabilityPolicy.BEST_EFFORT)
        self.create_subscription(Odometry, '/odom', self.on_odom, latest_sensor)
        self.create_subscription(Bool, '/third_impact/manual_control', self.on_manual, durable)
        for key in ('front', 'rear'):
            self.create_subscription(LaserScan, self.p[key+'_scan'],
                                     lambda msg, k=key: self.on_scan(k, msg), latest_sensor)
        self._nav_sample = None
        self._nav_accept_after = 0.
        self.tf_node.create_subscription(Twist, '/third_impact/nav_cmd_vel', self.on_nav_velocity, 1)
        self.create_subscription(Empty, '/go_to_goal', self.on_return, 10)
        self.create_subscription(Empty, '/go_around', self.on_patrol, 10)
        self.create_subscription(Empty, '/save_goal', self.on_save, 10)
        self.create_subscription(Empty, '/third_impact/save_region', self.on_region, 10)
        self.create_subscription(Empty, '/third_impact/save_boundary', self.on_boundary, 10)
        self.create_subscription(String, '/third_impact/set_zone', self.on_set_zone, 10)
        self.nav = ActionClient(self, NavigateToPose, '/navigate_to_pose')
        self.init_return()
        self.init_patrol()
        self.init_patrol_planning()
        self.patrol_planner = ActionClient(self, ComputePathToPose, '/compute_path_to_pose')
        self.create_subscription(Costmap, '/global_costmap/costmap_raw', self.on_patrol_costmap, 1)
        self.create_subscription(NavPath, '/plan', self.on_patrol_path, 10)
        self.editor = RectangleEditor(self) if self.mode == 'teach' else None
        self.boundary_editor = RectangleEditor(self, 'third_impact/boundary', (.1,.7,1.), 4.) if self.mode == 'teach' else None
        self.navigation_map_key = None
        self.load()
        self.create_timer(.05, self.timed_tick)  # serial driver integrates commands at 20 Hz
        self.create_timer(.2, self.publish_status)
        self.create_timer(.5, self.publish_markers)

    def event(self, text):
        self.detail = text
        self.get_logger().info(text)
        self.event_pub.publish(String(data=json.dumps({'time': time.time(), 'state': self.state, 'message': text})))

    def load(self):
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text())
            pose = np.asarray(data['goal'], dtype=float)
            reference = np.asarray(data['reference'], dtype=float)
            rect = data.get('rectangle')
            if data['map_fingerprint'] != self.fingerprint:
                self.event('Saved map differs: teach a new goal and rectangle')
                return
            if (pose.shape != (3,) or not np.isfinite(pose).all() or reference.ndim != 2
                    or reference.shape[1] != 2 or len(reference) < 50 or not np.isfinite(reference).all()):
                raise ValueError('Invalid goal/reference')
            if rect is not None and (len(rect) != 5 or not np.isfinite(rect).all() or min(rect[3:]) <= 0):
                raise ValueError('Invalid rectangle')
            if data.get('region_policy') != 'boundary_and_exclusion':
                # Do not reinterpret either earlier one-region format.
                data['rectangle'] = None
                data['boundary'] = None
            boundary = data.get('boundary')
            if boundary is not None and (len(boundary) != 5 or not np.isfinite(boundary).all() or min(boundary[3:]) <= 0):
                raise ValueError('Invalid boundary')
            self.data = data
            if self.editor:
                self.editor.reset(data['goal'], data.get('rectangle'))
                self.boundary_editor.reset(data['goal'], boundary)
            self.event('목표 불러옴. 한계 구간과 정찰 제한 구간을 각각 확인·저장하세요.')
        except (ValueError, KeyError, OSError, TypeError) as exc:
            self.event(f'Cannot load goal: {exc}; teach again')

    def on_map(self, msg):
        self.grid = msg
        values = np.asarray(msg.data).reshape(msg.info.height, msg.info.width)
        free = values == 0
        # Unknown space and map boundaries are obstacles for candidate selection.
        distance = distance_transform_edt(np.pad(free, 1, constant_values=False))[1:-1, 1:-1]*msg.info.resolution
        self.candidates = np.argwhere(distance >= self.p['patrol_clearance'])

    def on_amcl(self, msg):
        self.amcl, self.amcl_stamp = msg, time.monotonic()

    def on_localization(self, msg):
        try:
            info = json.loads(msg.data)
            if isinstance(info, dict):
                self.localization_info, self.localization_stamp = info, time.monotonic()
        except (ValueError, TypeError):
            pass

    def on_odom(self, msg):
        self.odom_processing_age = self.message_age(msg.header.stamp)
        self.odom, self.odom_stamp = msg, time.monotonic()

    def on_manual(self, msg):
        previous = self.manual
        self.manual, self.manual_stamp = msg.data, time.monotonic()
        if previous != self.manual:
            self.settled_since = None
            self.align_last_key = None
            if self.manual:
                self.cancel_nav()
                self.stop()
                if self.state == 'CAPTURE':
                    self.abort_capture('Manual mode changed during capture')
            self.event('Manual control: mission paused' if self.manual else 'Automatic control: resume mission')

    def on_nav_velocity(self, msg):
        self._nav_sample = (msg, time.monotonic(), time.time())

    def message_age(self, stamp):
        return (self.get_clock().now().nanoseconds-Time.from_msg(stamp).nanoseconds)*1e-9

    def on_scan(self, key, msg):
        if not hasattr(self, 'scan_diagnostics'):
            self.scan_diagnostics = {}
        entry = dict(received_age=self.message_age(msg.header.stamp), error=None)
        self.scan_diagnostics[key] = entry
        try:
            if not 0. <= entry['received_age'] <= self.p['scan_timeout']:
                entry['error'] = 'stamp_out_of_range'
                return
            tf = self.tf.lookup_transform('base_link', msg.header.frame_id, Time.from_msg(msg.header.stamp))
            q = tf.transform.rotation
            # Full quaternion rotation: both lidar mounts include a pi pitch.
            x, y, z, w = q.x, q.y, q.z, q.w
            rotation = np.array([[1-2*(y*y+z*z), 2*(x*y-z*w)],
                                 [2*(x*y+z*w), 1-2*(x*x+z*z)]])
            ranges = np.asarray(msg.ranges)
            angles = msg.angle_min+np.arange(len(ranges))*msg.angle_increment
            valid = np.isfinite(ranges) & (ranges > max(msg.range_min, .10)) & (ranges < min(msg.range_max, self.p['scan_max_range']))
            points = np.column_stack((ranges[valid]*np.cos(angles[valid]), ranges[valid]*np.sin(angles[valid])))
            t = tf.transform.translation
            points = points @ rotation.T+[t.x, t.y]
            # Remove own chassis returns, but retain all external obstacles.
            points = points[(abs(points[:, 0]) > self.p['footprint_half_length']) |
                            (abs(points[:, 1]) > self.p['footprint_half_width'])]
            sample = (time.monotonic(), msg.header.stamp, points)
            self.scans[key] = sample
            self.scan_pairs.add(key, Time.from_msg(msg.header.stamp).nanoseconds*1e-9, sample)
        except TransformException as exc:
            entry['error'] = 'tf: '+str(exc)

    def pose(self):
        if getattr(self, '_pose_snapshot_active', False):
            return self._pose_snapshot
        now_ns = self.get_clock().now().nanoseconds
        now = now_ns*1e-9
        self._pose_stamp_ns = None
        diagnostic = dict(checked_at=time.time(), source='unavailable')
        self.pose_diagnostics = diagnostic
        try:
            tf = self.tf.lookup_transform('map', 'base_link', Time())
            stamp_ns = Time.from_msg(tf.header.stamp).nanoseconds
            stamp = stamp_ns*1e-9  # Display and bounded odometry bridge only; never reconstruct TF time.
            age = (now_ns-stamp_ns)*1e-9
            diagnostic.update(tf_age=age, tf_stamp=stamp, tf_stamp_ns=stamp_ns)
            if not 0. <= age <= self.p['pose_timeout']:
                diagnostic['error'] = 'tf_stamp_out_of_range'
            else:
                t = tf.transform.translation
                pose = np.array([t.x, t.y, yaw(tf.transform.rotation)])
                if not np.isfinite(pose).all():
                    diagnostic['error'] = 'nonfinite_pose'
                    return None
                self._pose_stamp_ns = stamp_ns
                diagnostic['source'] = 'tf'
                # Anchor odometry at exactly the map pose timestamp, not reception time.
                try:
                    ot = self.tf.lookup_transform('odom', 'base_link', Time.from_msg(tf.header.stamp))
                    o = ot.transform.translation
                    self.pose_bridge.update(pose, [o.x,o.y,yaw(ot.transform.rotation)], stamp)
                except TransformException as exc:
                    diagnostic['anchor_error'] = str(exc)
                return pose
        except TransformException as exc:
            diagnostic.update(error=type(exc).__name__, exception=str(exc))
        odom = self.odom
        if odom is None or odom.header.frame_id != 'odom' or odom.child_frame_id != 'base_link':
            diagnostic['bridge_rejection'] = 'odom_missing_or_frame_mismatch'
            return None
        # Only return transit can bridge; docking/arrival always require direct TF.
        if self.intent != 'return' or self.return_phase != 'FORWARD' or self.manual:
            diagnostic['bridge_rejection'] = 'not_return_transit'
            return None
        op = odom.pose.pose
        stamp = Time.from_msg(odom.header.stamp).nanoseconds*1e-9
        estimate, reason = self.pose_bridge.estimate(
            [op.position.x,op.position.y,yaw(op.orientation)], stamp, now)
        diagnostic['bridge_result'] = reason
        if estimate is not None:
            diagnostic['source'] = reason
        return estimate

    def selected_scans(self):
        if getattr(self, '_pose_snapshot_active', False):
            return self._scan_snapshot
        return self.scan_pairs.select(self.get_clock().now().nanoseconds*1e-9, self.p['scan_timeout'])

    def cloud(self):
        def unavailable(reason):
            self.scan_motion_error = reason
            if getattr(self, '_pose_snapshot_active', False):
                self._cycle_ready = False
                self.health_diagnostics = dict(getattr(self, 'health_diagnostics', {}),
                                               reason='scan_motion_tf_unavailable')
            return None
        scans = self.selected_scans()
        if scans is None or any(not 0. <= self.message_age(scans[k][1]) <= self.p['scan_timeout'] for k in scans):
            return unavailable('scan_pair_missing_or_expired')
        if getattr(self, '_cycle_cloud_done', False) and self._pose_snapshot_active:
            return self._cycle_cloud
        if getattr(self, '_pose_snapshot_active', False):
            self._cycle_cloud_done = True
            self._cycle_cloud = None
        try:
            target = (Time(nanoseconds=self._pose_stamp_ns).to_msg()
                      if getattr(self, '_pose_snapshot_active', False) and getattr(self, '_pose_stamp_ns', None) is not None
                      else self.tf.lookup_transform('odom','base_link',Time()).header.stamp)
            if not 0. <= self.message_age(target) <= self.p['odom_timeout']:
                return unavailable('motion_target_expired')
            key = (*[Time.from_msg(scans[k][1]).nanoseconds for k in ('front','rear')],
                   Time.from_msg(target).nanoseconds)
            if getattr(self, '_cloud_key', None) != key:
                clouds = []
                for k in ('front','rear'):
                    motion = self.tf.lookup_transform_full('base_link', Time.from_msg(target),
                        'base_link', Time.from_msg(scans[k][1]), 'odom').transform
                    clouds.append(apply_transform(np.c_[scans[k][2], np.zeros(len(scans[k][2]))],motion)[:,:2])
                self._cloud_value = voxel(np.vstack(clouds))
                self._cloud_key = key
            self.scan_motion_error = None
            self._used_scan_key = key[:2]
            self._cycle_cloud = self._cloud_value
            return self._cloud_value
        except TransformException as exc:
            return unavailable(str(exc))

    def scan_key(self):
        scans = self.selected_scans()
        if scans is None:
            return (0, 0)
        return tuple(Time.from_msg(scans[k][1]).nanoseconds for k in ('front','rear'))

    def healthy(self):
        if getattr(self, '_pose_snapshot_active', False) and getattr(self, '_cycle_ready', None) is not None:
            return self._cycle_ready
        ready = ImpactNode._evaluate_health(self)
        if getattr(self, '_pose_snapshot_active', False):
            self._cycle_ready = ready
        return ready

    def _evaluate_health(self):
        now = time.monotonic()
        self.health_diagnostics = dict(reason='ready',
            odom_age=now-self.odom_stamp, odom_source_age=getattr(self, 'odom_processing_age', None), manual_age=now-self.manual_stamp,
            localization_age=now-self.localization_stamp,
            scans=getattr(self,'scan_diagnostics',{}))
        def fail(reason):
            self.health_diagnostics['reason'] = reason
            self.health_diagnostics['pose'] = getattr(self, 'pose_diagnostics', {})
            return False
        if self.p['require_localization_ready'] and (
                now-self.localization_stamp > 2. or not self.localization_info.get('ready', False)):
            return fail('localization_not_ready')
        if self.manual is None or now-self.manual_stamp > 1.:
            return fail('manual_state_stale')
        if self.odom is None or now-self.odom_stamp > self.p['odom_timeout']:
            return fail('odom_stale')
        if self.amcl is None:
            return fail('amcl_missing')
        cov = self.amcl.pose.covariance
        if not np.isfinite(cov).all() or max(cov[0], cov[7]) > self.p['amcl_max_xy_variance'] or cov[35] > self.p['amcl_max_yaw_variance']:
            return fail('amcl_covariance')
        if self.pose() is None:
            return fail('pose_tf_unavailable_or_stale')
        scans = self.selected_scans()
        if scans is None:
            return fail('scan_pair_unavailable')
        self.health_diagnostics['scan_pair_delta'] = abs(
            Time.from_msg(scans['front'][1]).nanoseconds-Time.from_msg(scans['rear'][1]).nanoseconds)*1e-9
        if self.cloud() is None:
            return fail('scan_motion_tf_unavailable')
        if self.grid is None:
            return fail('map_missing')
        return True

    def timed_tick(self):
        start = time.monotonic()
        try:
            self._cycle_cloud_done = False
            self._cycle_ready = None
            sample = getattr(self, '_nav_sample', None)
            if sample is not None and sample[1] > self._nav_accept_after:
                self.nav_velocity, self.nav_velocity_stamp = sample[:2]
            self._pose_snapshot = self.pose()
            self._scan_snapshot = self.selected_scans()
            self._pose_snapshot_active = True
            self.tick()
            # Stationary modes still publish a current sensor assessment.
            if self._cycle_ready is None and hasattr(self, 'healthy'):
                self._cycle_ready = self.healthy()
        finally:
            self.control_status = dict(ready=bool(getattr(self, '_cycle_ready', False)),
                health=copy.deepcopy(getattr(self, 'health_diagnostics', {})),
                pose=copy.deepcopy(getattr(self, 'pose_diagnostics', {})),
                checked_at=time.time())
            self._pose_snapshot_active = False
            self.last_tick_ms = (time.monotonic()-start)*1000.
            self.max_tick_ms = max(getattr(self,'max_tick_ms',0.), self.last_tick_ms)

    def stopped(self):
        if self.odom is None or time.monotonic()-self.odom_stamp > self.p['odom_timeout']:
            return False
        t = self.odom.twist.twist
        return math.hypot(t.linear.x, t.linear.y) < self.p['stopped_linear'] and abs(t.angular.z) < self.p['stopped_angular']

    def odom_pose(self):
        pose = self.odom.pose.pose
        return np.array([pose.position.x, pose.position.y, yaw(pose.orientation)])

    def on_save(self, _):
        if self.mode != 'teach' or self.state == 'CAPTURE':
            return
        if not self.healthy() or not self.stopped():
            self.event('Save rejected: localization and both scans must be valid; stop the robot first')
            return
        self.state = 'CAPTURE'; self.capture_start = time.monotonic()
        self.capture_pose = self.pose(); self.capture_clouds = []; self.capture_signature = None
        self.capture_odom = self.odom_pose()
        self.event('Capturing stationary lidar reference; do not move')

    def abort_capture(self, reason):
        self.state = 'TEACH'; self.capture_clouds = []
        self.event('Capture rejected: '+reason+'; previous saved goal unchanged')

    def capture(self):
        pose, cloud = self.pose(), self.cloud()
        if not self.healthy() or not self.stopped() or pose is None or cloud is None:
            self.abort_capture('robot moved or sensor/localization data missing')
            return
        delta = relative(self.capture_pose, pose)
        odom_delta = relative(self.capture_odom, self.odom_pose())
        if np.linalg.norm(odom_delta[:2]) > .002 or abs(odom_delta[2]) > math.radians(.3):
            self.abort_capture('odometry detected motion during reference capture')
            return
        if np.linalg.norm(delta[:2]) > .005 or abs(delta[2]) > math.radians(1):
            self.abort_capture('pose changed during capture')
            return
        key = self.scan_key()
        if key != self.capture_signature:
            self.capture_clouds.append(cloud); self.capture_signature = key
        if time.monotonic()-self.capture_start < self.p['capture_seconds']:
            return
        if len(self.capture_clouds) < 8:
            self.abort_capture('not enough fresh scan pairs')
            return
        reference = voxel(np.vstack(self.capture_clouds))
        test = register_scan(cloud, reference, [0., 0., 0.])
        if not self.match_ok(test):
            self.abort_capture('surrounding geometry is insufficient for reliable alignment')
            return
        data = {'version': 2, 'map_fingerprint': self.fingerprint, 'map_file': self.p['map_file'],
                'saved_at': time.time(), 'goal': self.capture_pose.tolist(),
                'reference': reference.tolist(), 'rectangle': None, 'boundary': None, 'region_policy': 'boundary_and_exclusion'}
        try:
            atomic_save(self.path, data)
        except OSError as exc:
            self.abort_capture(str(exc)); return
        self.data = data; self.state = 'TEACH'
        self.editor.reset(data['goal'])
        self.boundary_editor.reset(data['goal'])
        self.event('목표 저장 완료. 파란 한계 구간과 주황 정찰 제한 구간을 각각 편집·저장하세요.')


    def on_set_zone(self, msg):
        """UI에서 보낸 구역 좌표를 저장한다. 임무 모드에서도 받는다."""
        try:
            body = json.loads(getattr(msg, "data", "") or "{}")
        except json.JSONDecodeError:
            self.event("구역 형식이 잘못되었습니다.")
            return
        kind = str(body.get("kind") or "")
        if kind in {"region", "rectangle", "exclusion"}:
            key = "rectangle"
        elif kind in {"boundary", "limit"}:
            key = "boundary"
        else:
            self.event("구역 종류를 알 수 없습니다.")
            return
        try:
            rect = [
                float(body["x"]),
                float(body["y"]),
                float(body.get("yaw") or 0.0),
                float(body["width"]),
                float(body["height"]),
            ]
        except (KeyError, TypeError, ValueError):
            self.event("구역 좌표가 잘못되었습니다.")
            return
        if self.mode == "teach" and self.state == "CAPTURE":
            return
        if not self.data or self.data.get("goal") is None:
            self.event("목표를 먼저 저장하세요.")
            return
        if not np.isfinite(rect).all() or min(rect[3:]) <= 0.:
            self.event("유효한 구간을 지정하세요.")
            return
        radius = math.hypot(self.p["footprint_half_length"], self.p["footprint_half_width"]) + .05
        if key == "boundary" and not inside_patrol_region(self.data["goal"], rect, radius):
            self.event("한계 구간 안에 복귀 목표와 로봇 몸체 여유를 포함해야 합니다.")
            return
        data = dict(self.data, **{key: list(rect)}, region_policy="boundary_and_exclusion")
        try:
            atomic_save(self.path, data)
        except OSError as exc:
            self.event("구간 저장 실패: " + str(exc))
            return
        self.data = data
        editor = self.editor if key == "rectangle" else self.boundary_editor
        if editor is not None:
            editor.rect = list(rect)
            editor.render()
        self.event(("한계 구간" if key == "boundary" else "정찰 제한 구간") + " 저장 완료")

    def on_boundary(self, _):
        self.save_zone('boundary', self.boundary_editor)

    def on_region(self, _):
        self.save_zone('rectangle', self.editor)

    def save_zone(self, key, editor):
        if self.mode != 'teach' or not self.data or self.state == 'CAPTURE':
            return
        rect = editor.rect
        if rect is None or len(rect) != 5 or not np.isfinite(rect).all() or min(rect[3:]) <= 0:
            self.event('유효한 구간을 지정하세요.'); return
        radius = math.hypot(self.p['footprint_half_length'], self.p['footprint_half_width'])+.05
        if key == 'boundary' and not inside_patrol_region(self.data['goal'], rect, radius):
            self.event('한계 구간 안에 복귀 목표와 로봇 몸체 여유를 포함해야 합니다.'); return
        data = dict(self.data, **{key: list(rect)}, region_policy='boundary_and_exclusion')
        try:
            atomic_save(self.path, data)
        except OSError as exc:
            self.event('구간 저장 실패: '+str(exc)); return
        self.data = data
        self.event(('한계 구간' if key == 'boundary' else '정찰 제한 구간')+' 저장 완료')

    def publish_navigation_map(self):
        if self.grid is None:
            return
        boundary = self.data.get('boundary') if self.data else None
        saved_exclusion = self.data.get('rectangle') if self.data else None
        exclusion = saved_exclusion
        # Initial pose inside the patrol exclusion may drive out. The saved
        # boundary still limits planning; only the exclusion paint is lifted.
        if self.intent == 'patrol' and saved_exclusion is not None:
            current = self.pose()
            # Before the first patrol arrival, and while leaving an initial
            # pose inside the exclusion, do not paint that zone as an obstacle.
            if current is not None and self._patrol_exclusion(current) is None and (
                    self.patrol_visits < 1 or getattr(self, '_initial_exclusion_exit', False)):
                exclusion = None
        if getattr(self, 'planning_anchor', None) is None:
            current = self.pose()
            if current is not None:
                self.planning_anchor = current.copy()
        anchor = getattr(self, 'planning_anchor', None)
        extra = getattr(self, 'patrol_extra_clearance', 0.) if self.intent == 'patrol' else 0.
        key = (id(self.grid), repr(boundary), repr(saved_exclusion), repr(exclusion), self.intent == 'return', extra, repr(anchor))
        if key == self.navigation_map_key:
            return
        msg = copy.deepcopy(self.grid)
        info = msg.info
        origin = [info.origin.position.x, info.origin.position.y, yaw(info.origin.orientation)]
        cells = np.asarray(msg.data).reshape(info.height, info.width)
        # Missing either saved zone blocks autonomous planning.
        cells = region_map(cells, info.resolution, origin,
                           boundary if saved_exclusion is not None else None,
                           exclusion if self.intent != 'return' else None)
        msg.data = cells.astype(np.int8).ravel().tolist()
        msg.header.stamp = self.get_clock().now().to_msg()
        self.navigation_map_pub.publish(msg)
        # Global planner's inscribed-cost radius already accounts for the
        # smaller body half-size. Add only the missing disk radius plus 10cm.
        planning = copy.deepcopy(msg)
        extra_margin = (math.hypot(self.p['footprint_half_length'], self.p['footprint_half_width'])
                        +.05+.10-min(self.p['footprint_half_length'], self.p['footprint_half_width'])+extra)
        planning.data = region_map(np.asarray(self.grid.data).reshape(info.height, info.width),
            info.resolution, origin, boundary if saved_exclusion is not None else None,
            exclusion if self.intent != 'return' else None, extra_margin, anchor).astype(np.int8).ravel().tolist()
        self.planning_map_pub.publish(planning)
        self.navigation_map_key = key

    def _patrol_exclusion(self, pose):
        """Patrol exclusion. Lifted until the first patrol is completed,
        and while leaving an initial pose that was set inside the zone.

        The boundary limit is unchanged.
        """
        rect = self.data.get('rectangle')
        if self.intent != 'patrol' or rect is None or pose is None:
            return None
        if self.patrol_visits < 1:
            if not getattr(self, '_first_patrol_zone_noted', False):
                self._first_patrol_zone_noted = True
                self.event('첫 정찰을 완수하기 전에는 정찰 제한 구간 안에서도 정찰합니다.')
            return None
        info = self.localization_info or {}
        if (info.get('state') == 'VERIFYING'
                and '사용자가 지정한 초기 위치' in str(info.get('reason') or '')):
            self._initial_exclusion_exit = None
        radius = math.hypot(self.p['footprint_half_length'], self.p['footprint_half_width'])+.05
        overlaps = not outside_exclusion(pose, rect, radius)
        if info.get('ready') and getattr(self, '_initial_exclusion_exit', None) is None:
            boundary = self.data.get('boundary')
            in_bounds = boundary is not None and inside_patrol_region(pose, boundary, 0.)
            self._initial_exclusion_exit = bool(overlaps and in_bounds)
            if self._initial_exclusion_exit:
                self.event('초기 위치가 정찰 제한 구간 안입니다. 구간 밖으로 이동을 허용합니다.')
        if getattr(self, '_initial_exclusion_exit', False) and not overlaps:
            self._initial_exclusion_exit = False
            self.event('정찰 제한 구간을 벗어났습니다. 이후에는 구간 안으로 들어가지 않습니다.')
        if getattr(self, '_initial_exclusion_exit', False):
            return None
        return rect

    def _at_saved_goal(self, pose):
        goal = np.asarray(self.data['goal'])
        return (np.linalg.norm(pose[:2]-goal[:2]) <= .06
                and abs(wrap(pose[2]-goal[2])) <= .15)

    def _complete_arrival(self, reason):
        self.intent = 'hold'
        self.state = 'HOLD'
        self.stop()
        self.cancel_nav()
        self.arrival_pub.publish(Empty())
        self.event(reason)

    def motion_allowed(self, pose, vx=0., vy=0., horizon=1., angular=0.):
        exclusion = self._patrol_exclusion(pose) if self.intent == 'patrol' else None
        return travel_allowed(pose, self.data.get('boundary'), exclusion,
                              vx, vy, self.p['footprint_half_length'],
                              self.p['footprint_half_width'], horizon, angular)

    def on_return(self, _):
        if self.mode != 'mission' or not accepts_return(self.intent) or self.intent == 'return':
            return
        self.patrol_retry_target = None
        self.init_patrol()
        self.reset_return()
        self.intent = 'return'; self.state = 'RETURN'; self.next_attempt = 0.
        self.cancel_nav(); self.stop(); self.settled_since = None
        self.event('Return requested; patrol cancelled')

    def on_patrol(self, _):
        if self.mode != 'mission' or not accepts_patrol(self.intent):
            return
        self.init_patrol()
        self.patrol_visits = 0
        self._first_patrol_zone_noted = False
        self.intent = 'patrol'; self.state = 'PATROL'; self.next_attempt = 0.
        self.event('Patrol requested')

    def stop(self, reason=None):
        # Publish the safety command before diagnostics or serialization.
        self.patrol_output = (0., 0.)
        self.patrol_output_stamp = time.monotonic()
        self.velocity_pub.publish(Twist())
        caller = inspect.currentframe().f_back
        age = time.monotonic()-getattr(self, 'nav_velocity_stamp', 0.)
        self.last_stop = dict(time=time.time(), reason=reason or caller.f_code.co_name,
            line=caller.f_lineno, state=getattr(self,'state',None),
            health=getattr(self,'health_diagnostics',{}).get('reason'),
            nav_command_age=age, scan_error=getattr(self,'scan_motion_error',None),
            scan_key=getattr(self,'_used_scan_key',None),
            nav_received_at=(getattr(self,'_nav_sample',None) or (None,None,None))[2])
        del caller
        if hasattr(self, 'event_pub'):
            self.event_pub.publish(String(data=json.dumps(dict(time=self.last_stop['time'],
                state=self.last_stop['state'], message='Velocity stop: '+self.last_stop['reason'],
                stop=self.last_stop))))

    def cancel_nav(self):
        self.cancel_patrol_plan()
        self.cancel_requested = True
        self.nav_velocity_stamp = 0.
        self._nav_accept_after = time.monotonic()
        if self.nav_handle is not None:
            # Result callback is the handoff barrier, not cancellation acceptance.
            if not getattr(self, 'cancel_sent', False):
                self.cancel_sent = True
                self.nav_handle.cancel_goal_async()

    def send_nav(self, pose, purpose):
        if self.nav_pending or self.nav_handle is not None:
            return
        if not self.nav.server_is_ready():
            self.detail = 'Waiting for Nav2 action server'
            return
        request = NavigateToPose.Goal(); request.pose = self.pose_message(pose)
        request.behavior_tree = self.p['patrol_bt_file' if purpose == 'patrol' else 'return_bt_file']
        if purpose == 'patrol':
            self.patrol_path = None; self.patrol_path_stamp = 0.
        self.nav_pending = True; self.cancel_requested = False; self.cancel_sent = False
        self.patrol_reached = False
        self.nav_purpose = purpose; self.nav_target = list(pose)
        self.nav_velocity_stamp = 0.
        self.detail = 'Navigation request pending'
        self.nav.send_goal_async(request).add_done_callback(self.nav_accepted)

    def nav_accepted(self, future):
        self.nav_pending = False
        try:
            handle = future.result()
        except Exception as exc:
            self.event(f'Navigation submission failed; retrying: {exc}')
            self.next_attempt = time.monotonic()+self.p['retry_seconds']; return
        if not handle.accepted:
            self.next_attempt = time.monotonic()+self.p['retry_seconds']
            self.event('Navigation rejected; retrying'); return
        self.nav_handle = handle
        handle.get_result_async().add_done_callback(self.nav_finished)
        if self.cancel_requested or self.manual or self.nav_purpose != self.intent:
            self.cancel_nav()
        else:
            self.event('Navigation accepted: ' + self.nav_purpose)

    def nav_finished(self, future):
        purpose, cancelled = self.nav_purpose, self.cancel_requested
        self.nav_handle = None; self.nav_velocity_stamp = 0.
        try:
            status = future.result().status
        except Exception as exc:
            self.event(f'Navigation result unavailable; retrying: {exc}')
            status = GoalStatus.STATUS_UNKNOWN
        patrol_arrived = purpose == 'patrol' and (self.patrol_reached or status == GoalStatus.STATUS_SUCCEEDED)
        self.last_navigation_result = {'purpose': purpose, 'status': int(status),
                                       'cancel_requested': bool(cancelled),
                                       'patrol_arrived': patrol_arrived}
        names = {0: '결과 확인 불가', 1: '수락됨', 2: '실행 중', 3: '취소 처리 중',
                 4: '이동 성공', 5: '이동 취소', 6: '이동 실패'}
        if patrol_arrived:
            self.patrol_retry_target = None
            self.init_patrol()
            self.patrol_visits += 1
            self.event('정찰 목적지 도착: 다음 목적지를 선택합니다.')
        else:
            self.event(('정찰' if purpose == 'patrol' else '복귀') + ': ' + names.get(status, '결과 확인 불가'))
        if purpose == 'return' and self.intent == 'return' and not cancelled and status == GoalStatus.STATUS_SUCCEEDED:
            self.state = 'ALIGN'; self.align_last_key = None
        self.next_attempt = time.monotonic()+(0. if patrol_arrived or purpose != self.intent else self.p['retry_seconds'])
        if (purpose == 'patrol' and self.intent == 'patrol' and not cancelled and not patrol_arrived
                and self.nav_target is not None):
            if getattr(self, 'patrol_path', None) is None:
                self.reject_patrol_target(self.nav_target, '이동 시작 전 Nav2 실패: 목표 재검사 필요')
            else:
                self.begin_patrol_recovery()

    def patrol_target(self):
        if self.candidates is None or not len(self.candidates):
            return None
        origin = self.grid.info.origin
        base = [origin.position.x, origin.position.y, yaw(origin.orientation)]
        current = self.pose()
        if current is None:
            return None
        costmap = self.patrol_costmap
        if costmap is None or time.monotonic()-self.patrol_costmap_stamp > 2.:
            self.detail = '최신 전역 비용 지도 대기: 도달 가능한 목표 확인 전 정지'
            return None
        connected = costmap_component(costmap, current)
        info = costmap.metadata
        cost_origin = [info.origin.position.x, info.origin.position.y, yaw(info.origin.orientation)]
        now = time.monotonic()
        self.patrol_rejected = [(p, end) for p, end in self.patrol_rejected if end > now]
        margin = math.hypot(self.p['footprint_half_length'], self.p['footprint_half_width'])+.05
        for index in np.random.permutation(len(self.candidates)):
            row, col = self.candidates[index]
            p = transform(np.array([[(col+.5)*self.grid.info.resolution, (row+.5)*self.grid.info.resolution]]), base)[0]
            local = relative(cost_origin, [*p, 0.])
            cx, cy = math.floor(local[0]/info.resolution), math.floor(local[1]/info.resolution)
            if not (0 <= cx < info.size_x and 0 <= cy < info.size_y and connected[cy, cx]):
                continue
            if any(np.linalg.norm(p-np.asarray(rejected)) < .5 for rejected, _ in self.patrol_rejected):
                continue
            if (inside_patrol_region(p, self.data['boundary'], margin)
                    and outside_exclusion(p, self.data['rectangle'], margin)
                    and np.linalg.norm(p-current[:2]) > .6):
                return [*p, random.uniform(-math.pi, math.pi)]
        self.detail = '이동 가능한 정찰 목표 없음: 비용 지도 갱신 후 재탐색'
        return None

    def match_ok(self, result):
        return (result is not None and result['rmse'] <= self.p['match_rmse']
                and result['overlap'] >= self.p['match_overlap']
                and result['observability'] >= self.p['match_observability']
                and result['condition'] <= self.p['match_max_condition'])

    def align(self):
        cloud = self.cloud(); pose = self.pose()
        if cloud is None or pose is None:
            self.stop(); self.settled_since = None; return
        if self.tick_micro_adjustment(pose):
            return
        if self.position_return(pose):
            return
        if self.coarse_align(pose):
            return
        key = self.scan_key()
        now = time.monotonic()
        if self.fine_alignment_started is None:
            self.fine_alignment_started = now
        if now-self.fine_alignment_started > 15.:
            self.reapproach_return('정밀 정렬 수렴 시간 초과 또는 기준 스캔 불일치')
            return
        if now < self.alignment_recheck_until:
            self.stop()
            self.detail = '정렬 재검증 대기: ' + self.alignment_diagnostics.get('rejection','')
            return
        if key != self.align_last_key:
            self.align_last_key = key
            initial = relative(self.data['goal'], pose)
            result = register_scan(cloud, self.data['reference'], initial)
            self.match = result
            reasons = []
            diagnostic = dict(phase='FINE_VERIFY',initial=initial.tolist(),
                longitudinal_m=float(initial[0]), lateral_m=float(initial[1]),
                heading_error_deg=math.degrees(initial[2]), updated_at=time.time(),
                attempts=self.return_reapproaches)
            if result is None:
                reasons.append('LiDAR 대응점 부족')
            else:
                correction = relative(initial,result['pose'])
                distance = float(np.linalg.norm(correction[:2]))
                angle = abs(math.degrees(correction[2]))
                diagnostic.update(correction_m=distance,correction_deg=angle,
                    correction_limit_m=self.p['match_max_correction'],
                    correction_limit_deg=self.p['match_max_angle_deg'])
                for value,limit,name,upper in (
                    (result['rmse'],self.p['match_rmse'],'RMSE',True),
                    (result['overlap'],self.p['match_overlap'],'중첩률',False),
                    (result['observability'],self.p['match_observability'],'관측성',False),
                    (result['condition'],self.p['match_max_condition'],'조건수',True)):
                    if (value>limit if upper else value<limit):
                        reasons.append(f'{name} {value:.3f} (기준 {limit:.3f})')
                if distance>self.p['match_max_correction']:
                    reasons.append(f"위치 보정량 {distance*100:.1f}cm > 허용 {self.p['match_max_correction']*100:.1f}cm")
                if angle>self.p['match_max_angle_deg']:
                    reasons.append(f"방향 보정량 {angle:.1f}° > 허용 {self.p['match_max_angle_deg']:.1f}°")
            self.alignment_diagnostics = diagnostic
            if reasons:
                self.alignment_valid_count=0;self.alignment_last_pose=None
                self.align_velocity=(0.,0.);self.settled_since=None
                self.alignment_reject_since = self.alignment_reject_since or now
                diagnostic['rejection']=' / '.join(reasons)
                self.detail='정렬 거부: '+diagnostic['rejection']
                if now-self.alignment_reject_since>=5.:
                    diagnostic['phase']='REVERIFY'
                    self.alignment_recheck_until=now+2.
                    self.alignment_reject_since=None
                    self.detail='정렬 재검증: '+diagnostic['rejection']+' — 기준 스캔·현재 위치 확인 필요'
                self.stop(); return
            self.alignment_reject_since=None
            # Require independent fresh observations after coarse alignment or rejection.
            if self.alignment_valid_count<3:
                previous=self.alignment_last_pose
                consistent=(previous is None or (np.linalg.norm(result['pose'][:2]-previous[:2])<.03
                             and abs(wrap(result['pose'][2]-previous[2]))<math.radians(3)))
                self.alignment_valid_count=self.alignment_valid_count+1 if consistent else 1
                self.alignment_last_pose=np.asarray(result['pose']).copy()
                self.stop();self.align_velocity=(0.,0.)
                self.detail=f'정밀 정합 재검증: 연속 유효 관측 {self.alignment_valid_count}/3'
                return
            diagnostic['phase']='FINE_ALIGN'
            self.align_valid_stamp = now
            error = result['pose']
            diagnostic.update(longitudinal_m=float(error[0]), lateral_m=float(error[1]),
                              heading_error_deg=math.degrees(error[2]), reference='saved_scan')
            xy = np.linalg.norm(error[:2]); heading = abs(error[2])
            satisfied = xy < self.p['xy_tolerance'] and heading <= math.radians(self.p['yaw_tolerance_deg'])
            if satisfied:
                self.align_velocity = (0., 0.)
                if self.stopped():
                    self.settled_since = self.settled_since or now
                    if now-self.settled_since >= self.p['settle_seconds']:
                        self.intent = 'hold'; self.state = 'HOLD'; self.stop()
                        self.arrival_pub.publish(Empty())
                        self.event('Arrival emitted once; holding until a new go_around command')
                        return
                else:
                    self.settled_since = None
            else:
                self.settled_since = None
                if xy > self.p['xy_tolerance']:
                    bearing = wrap(math.atan2(-error[1], -error[0])-error[2])
                    if min(abs(bearing), math.pi-abs(bearing)) > .25:
                        self.start_micro_adjustment(pose, error)
                        return
                self.align_velocity = alignment_command(error, self.p['xy_tolerance'],
                    math.radians(self.p['yaw_tolerance_deg']), self.p['max_align_v'], self.p['max_align_w'])
            self.detail = f'Alignment estimate: {xy*1000:.1f} mm, {math.degrees(error[2]):.2f} deg'
        if now-self.align_valid_stamp > .2:
            self.stop(); self.settled_since = None; return
        v, w = self.align_velocity
        self.return_command(pose, v, w, True)

    def tick(self):
        self.publish_navigation_map()
        if self.mode == 'teach':
            self.stop()
            if self.state == 'CAPTURE':
                self.capture()
            return
        if self.intent == 'hold':
            self.state = 'HOLD'; self.stop(); return
        paused = self.manual or not self.healthy()
        if self.intent == 'patrol':
            self.patrol_readiness_pause(paused)
        if paused:
            if self.intent == 'return':
                self.pause_return()
            self.stop(); self.cancel_nav(); self.settled_since = None
            self.detail = 'Paused: manual control or localization/sensor readiness'
            self.last_readiness_stop = dict(time=time.time(), manual=self.manual,
                health=copy.deepcopy(getattr(self, 'health_diagnostics', {})),
                pose=copy.deepcopy(getattr(self, 'pose_diagnostics', {})))
            return
        if self.data is None or self.data.get('rectangle') is None or self.data.get('boundary') is None:
            self.state = 'WAIT'; self.stop()
            self.detail = '목표·한계 구간·정찰 제한 구간을 make_goal_launch에서 저장하세요.'
            return
        current_pose = self.pose()
        if current_pose is None:
            if self.intent == 'return':
                self.pause_return()
            self.stop(); self.cancel_nav(); self.settled_since = None
            self.detail = 'Paused: manual control or localization/sensor readiness'
            self.last_readiness_stop = dict(time=time.time(), manual=self.manual,
                health=copy.deepcopy(getattr(self, 'health_diagnostics', {})),
                pose=copy.deepcopy(getattr(self, 'pose_diagnostics', {})))
            return
        if not self.motion_allowed(current_pose):
            self.stop(); self.cancel_nav()
            self.detail = '현재 위치가 한계 구간 밖이거나 정찰 제한 구간 안입니다. 수동으로 이동해 주세요.'
            return
        if getattr(self, '_initial_exclusion_exit', False):
            self.detail = '초기 위치가 정찰 제한 구간 안입니다. 구간 밖으로 이동합니다.'
        busy = self.nav_pending or self.nav_handle is not None
        if self.intent == 'return':
            self.tick_return(current_pose)
            return
        else:
            if self.tick_patrol_recovery(current_pose):
                return
            self.state = 'PATROL' if busy else 'PATROL_SEARCH'
            if self.patrol_progress_pose is None or np.linalg.norm(current_pose[:2]-self.patrol_progress_pose[:2]) >= .20:
                self.patrol_progress_pose = current_pose.copy()
                self.patrol_progress_time = time.monotonic()
                self.patrol_recovery_count = 0
            if getattr(self, '_progress_watch', None) is None:
                self._progress_watch = ProgressWatch()
            nav_command = getattr(self, 'nav_velocity', None)
            fresh = (nav_command is not None and time.monotonic()-getattr(self, 'nav_velocity_stamp',0.) < .2
                     and not getattr(self, 'patrol_sensor_output_wait', False))
            command = (nav_command.linear.x, nav_command.angular.z) if nav_command is not None else (0.,0.)
            excluded, rotated = self._progress_watch.update(time.monotonic(), current_pose, command, fresh)
            self.patrol_progress_time += excluded
            if rotated:
                self.patrol_progress_time = time.monotonic()
            if busy and not self.cancel_requested and time.monotonic()-self.patrol_progress_time > 12.:
                self.begin_patrol_recovery(); return
            if (busy and self.nav_purpose == 'patrol' and self.nav_target is not None
                    and not self.cancel_requested
                    and np.linalg.norm(current_pose[:2]-np.asarray(self.nav_target[:2])) <= self.p['patrol_tolerance']):
                # Proximity alone completes patrol. Do not wait for yaw or settle.
                # Cancel/result handoff still prevents simultaneous controllers.
                self.patrol_reached = True
                self.cancel_nav(); self.stop()
                self.event(f"정찰 목적지 {self.p['patrol_tolerance']*100:g}cm 이내 도착: 이전 이동을 마무리하고 다음 목적지로 전환합니다.")
                return
            if not busy and self.patrol_plan_waiting():
                self.stop(); return
            if not busy and time.monotonic() >= self.next_attempt:
                target = getattr(self, 'patrol_retry_target', None)
                if target is None:
                    target = self.patrol_target()
                if target is not None:
                    self.request_patrol_plan(target)
                else:
                    self.next_attempt = time.monotonic()+self.p['retry_seconds']
                    self.state = 'PATROL_SEARCH'
        if (self.nav_handle is not None and not self.cancel_requested and self.nav_purpose == self.intent
                and time.monotonic()-self.nav_velocity_stamp < .2):
            command = copy.deepcopy(self.nav_velocity)
            # Reduce translation independently of steering. Do not replace the
            # patrol destination merely because one command approaches a zone.
            for scale in (1., .5, .25):
                vx, vy = self.nav_velocity.linear.x*scale, self.nav_velocity.linear.y*scale
                w = self.nav_velocity.angular.z
                if (self.motion_allowed(current_pose, vx, vy, angular=w)
                        and (scale == 1. or self.safe_patrol_command(current_pose, vx, w))):
                    command.linear.x, command.linear.y, command.angular.z = vx, vy, w
                    published = self.publish_patrol_command(current_pose, command)
                    if published and scale < 1.:
                        self.detail = '구간 경계 접근: 현재 정찰 목표를 유지하고 감속합니다.'
                    break
            else:
                self.begin_patrol_recovery()

        else:
            self.stop('nav_command_unavailable')

    def pose_message(self, pose):
        msg = PoseStamped(); msg.header.frame_id = 'map'; msg.header.stamp = self.get_clock().now().to_msg()
        msg.pose.position.x, msg.pose.position.y = float(pose[0]), float(pose[1])
        orientation(msg.pose, float(pose[2]))
        return msg

    def publish_status(self):
        result = {k: v.tolist() if isinstance(v, np.ndarray) else v for k, v in (self.match or {}).items()}
        busy = self.nav_pending or self.nav_handle is not None
        phase = ('cancel_requested' if busy and self.cancel_requested else
                 'requesting' if self.nav_pending else 'executing' if self.nav_handle is not None else 'idle')
        pose = getattr(self, '_pose_snapshot', None)
        distance = (float(np.linalg.norm(pose[:2]-np.asarray(self.nav_target[:2])))
                    if busy and pose is not None and self.nav_target is not None else None)
        msg = {'mode': self.mode, 'state': self.state, 'intent': self.intent, 'manual': self.manual,
               'ready': getattr(self, 'control_status', {}).get('ready', False),
               'health': getattr(self, 'control_status', {}).get('health', {}),
               'pose_tracking': getattr(self, 'control_status', {}).get('pose', {}),
               'control_checked_at': getattr(self, 'control_status', {}).get('checked_at'),
               'last_stop': getattr(self, 'last_stop', None),
               'last_readiness_stop': getattr(self, 'last_readiness_stop', None),
               'timing': {'tick_ms': getattr(self,'last_tick_ms',0.),
                          'max_tick_ms': getattr(self,'max_tick_ms',0.)},
               'patrol_control': {'near_phase': self.patrol_near['phase'] if self.patrol_near else None,
                                  'recovery_phase': self.patrol_recovery,
                                  'direction_stop_since': self.patrol_reversing_since,
                                  'wait_reason': self.patrol_wait_reason,
                                  'recovery_check': self.patrol_recovery_check,
                                  'sensor_retries': self.patrol_sensor_retries,
                                  'obstacle_recheck': self.patrol_obstacle_wait,
                                  'obstacle_clear_checks': self.patrol_obstacle_clear_count},
               'has_boundary': bool(self.data and self.data.get('boundary')),
               'has_goal': self.data is not None, 'has_rectangle': bool(self.data and self.data.get('rectangle')),
               'detail': self.detail, 'match': result, 'alignment': self.alignment_diagnostics, 'time': time.time(),
               'return': {'phase': self.return_phase, 'reverse_entry': self.return_reverse,
                          'blocked_seconds': (time.monotonic()-self.return_blocked if self.return_blocked else 0.),
                          'waiting': self.return_wait_reason,
                          'collision_check': self.return_collision},
               'localization': dict(self.localization_info,
                                    fresh=time.monotonic()-self.localization_stamp <= 2.),
               'navigation': {'phase': phase, 'purpose': self.nav_purpose, 'distance_m': distance,
                              'last_result': self.last_navigation_result, 'patrol_visits': self.patrol_visits,
                              'patrol_tolerance_m': self.p['patrol_tolerance']}}
        self.status_pub.publish(String(data=json.dumps(msg)))

    def publish_markers(self):
        markers = MarkerArray()
        clear = Marker(); clear.action = Marker.DELETEALL; markers.markers.append(clear)
        def add(pose, kind, ident, color, size):
            m = Marker(); m.header.frame_id = 'map'; m.header.stamp = self.get_clock().now().to_msg()
            m.ns = 'robot' if ident < 2 else 'saved_goal' if ident < 4 else 'patrol_region' if ident == 4 else 'travel_boundary' if ident == 6 else 'return_entry'
            m.id = ident; m.type = kind; m.action = Marker.ADD
            m.pose = self.pose_message(pose).pose; m.pose.position.z = .15
            m.scale.x, m.scale.y, m.scale.z = size
            m.color.r, m.color.g, m.color.b, m.color.a = color
            markers.markers.append(m)
        pose = self.pose()
        robot_size = (2 * self.p['footprint_half_length'],
                      2 * self.p['footprint_half_width'], .3)
        if pose is not None:
            add(pose, Marker.CUBE, 0, (.2, .7, 1., .8), robot_size)
            add(pose, Marker.ARROW, 1, (0., 0., 1., 1.), (.6, .08, .08))
        if self.data:
            goal = self.data['goal']; self.goal_pub.publish(self.pose_message(goal))
            add(goal, Marker.CUBE, 2, (.1, 1., .2, .4), robot_size)
            add(goal, Marker.ARROW, 3, (0., 1., 0., 1.), (.6, .08, .08))
            rect = self.data.get('rectangle')
            if rect:
                add(rect[:3], Marker.CUBE, 4, (1., .4, .1, .25), (rect[3], rect[4], .02))
        if self.data and self.data.get('boundary'):
            rect = self.data['boundary']
            add(rect[:3], Marker.CUBE, 6, (.1,.7,1.,.15), (rect[3],rect[4],.02))
        if self.intent == 'return' and self.return_reverse and self.return_entry is not None:
            add(self.return_entry, Marker.ARROW, 5, (1., 0., 1., 1.), (.6, .08, .08))
        self.marker_pub.publish(markers)


def main(args=None):
    rclpy.init(args=args)
    node = ImpactNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        if rclpy.ok():
            node.stop(); node.cancel_nav()
        node.listener.executor.shutdown()
        node.listener.dedicated_listener_thread.join()
        node.listener.unregister()
        node.tf_node.destroy_node()
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
