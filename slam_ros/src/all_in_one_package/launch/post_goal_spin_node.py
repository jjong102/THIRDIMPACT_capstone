#!/usr/bin/env python3
"""Post-goal behavior: spin in place after navigate_to_pose SUCCEEDED, then
on /diffusion_complete stop, turn to face away from the nearest wall, and
hold briefly. A new navigate_to_pose goal interrupts this at any point.

Publishes straight to /cmd_vel (bypassing velocity_smoother) so speed isn't
capped by velocity_smoother's max_velocity theta limit, which also bounds
normal DWB-driven turning. A local accel ramp keeps starts/turns smooth
despite skipping the smoother.

State machine:
  IDLE
    goal SUCCEEDED -> SPINNING
  SPINNING (ramp up to spin_angular_z on /cmd_vel)
    /diffusion_complete -> look up nearest wall in /global_costmap/costmap,
                            aim for the opposite heading -> ORIENTING
  ORIENTING (turn in place toward target_yaw using /amcl_pose feedback)
    within orient_yaw_tolerance -> HOLDING
  HOLDING (zero cmd_vel, wait hold_duration)
    timer elapses -> IDLE
  any state: new goal ACCEPTED/EXECUTING -> stop immediately -> IDLE
             (controller_server takes back /cmd_vel for the new goal)

Caution: this bypasses DWB and the local costmap while SPINNING/ORIENTING,
so there is no obstacle checking during those motions -- keep the speeds
sane for the space the robot stops in.
"""
import math
import sys

import rclpy
from rclpy.node import Node

from action_msgs.msg import GoalStatusArray
from geometry_msgs.msg import PoseWithCovarianceStamped, Twist
from nav_msgs.msg import OccupancyGrid
from std_msgs.msg import Empty

DEFAULT_SPIN_ANGULAR_Z = 2.094    # rad/s -- one full turn every ~3s (2*pi/3)
DEFAULT_ORIENT_ANGULAR_SPEED = 0.5   # rad/s while turning to face away from the wall
DEFAULT_ANGULAR_ACCEL = 4.0      # rad/s^2 ramp rate -- reaches spin_angular_z in ~0.5s
DEFAULT_ORIENT_YAW_TOLERANCE = 0.08  # rad (~4.6 deg)
DEFAULT_HOLD_DURATION = 1.0      # seconds to hold still once oriented
DEFAULT_WALL_SEARCH_RADIUS = 3.0     # meters, how far out to look for the nearest wall
DEFAULT_WALL_COST_THRESHOLD = 90     # occupancy cost counted as "wall" (0-100)
CMD_PUBLISH_RATE = 20.0          # Hz, matches controller_server.controller_frequency

GOAL_STATUS_ACCEPTED = 1
GOAL_STATUS_EXECUTING = 2
GOAL_STATUS_SUCCEEDED = 4

STATE_IDLE = 'IDLE'
STATE_SPINNING = 'SPINNING'
STATE_ORIENTING = 'ORIENTING'
STATE_HOLDING = 'HOLDING'


def yaw_from_quat(q):
    siny_cosp = 2.0 * (q.w * q.z + q.x * q.y)
    cosy_cosp = 1.0 - 2.0 * (q.y * q.y + q.z * q.z)
    return math.atan2(siny_cosp, cosy_cosp)


def normalize_angle(a):
    while a > math.pi:
        a -= 2.0 * math.pi
    while a < -math.pi:
        a += 2.0 * math.pi
    return a


class PostGoalSpinNode(Node):
    def __init__(self):
        super().__init__('post_goal_spin_node')

        self.declare_parameter('spin_angular_z', DEFAULT_SPIN_ANGULAR_Z)
        self.declare_parameter('orient_angular_speed', DEFAULT_ORIENT_ANGULAR_SPEED)
        self.declare_parameter('angular_accel', DEFAULT_ANGULAR_ACCEL)
        self.declare_parameter('orient_yaw_tolerance', DEFAULT_ORIENT_YAW_TOLERANCE)
        self.declare_parameter('hold_duration', DEFAULT_HOLD_DURATION)
        self.declare_parameter('wall_search_radius', DEFAULT_WALL_SEARCH_RADIUS)
        self.declare_parameter('wall_cost_threshold', DEFAULT_WALL_COST_THRESHOLD)

        self.spin_angular_z = self.get_parameter('spin_angular_z').value
        self.orient_angular_speed = self.get_parameter('orient_angular_speed').value
        self.angular_accel = self.get_parameter('angular_accel').value
        self.orient_yaw_tolerance = self.get_parameter('orient_yaw_tolerance').value
        self.hold_duration = self.get_parameter('hold_duration').value
        self.wall_search_radius = self.get_parameter('wall_search_radius').value
        self.wall_cost_threshold = self.get_parameter('wall_cost_threshold').value

        self.cmd_pub = self.create_publisher(Twist, '/cmd_vel', 10)

        self.state = STATE_IDLE
        self.current_angular_z = 0.0
        self.target_yaw = None
        self.last_status = None
        self.hold_timer = None

        self.robot_pose = None   # (x, y, yaw) in map frame, from /amcl_pose
        self.latest_costmap = None  # most recent /global_costmap/costmap

        self.create_subscription(
            GoalStatusArray, '/navigate_to_pose/_action/status',
            self.on_action_status, 10)
        self.create_subscription(
            Empty, '/diffusion_complete', self.on_diffusion_complete, 10)
        self.create_subscription(
            PoseWithCovarianceStamped, '/amcl_pose', self.on_amcl_pose, 10)
        self.create_subscription(
            OccupancyGrid, '/global_costmap/costmap', self.on_costmap, 1)

        self.create_timer(1.0 / CMD_PUBLISH_RATE, self.on_cmd_timer)

        self.get_logger().info(
            f'post_goal_spin_node ready (spin={self.spin_angular_z} rad/s, '
            f'orient={self.orient_angular_speed} rad/s, '
            f'hold={self.hold_duration}s after facing away from nearest wall)')

    # -- subscriptions -----------------------------------------------------

    def on_amcl_pose(self, msg):
        p = msg.pose.pose
        self.robot_pose = (p.position.x, p.position.y, yaw_from_quat(p.orientation))

    def on_costmap(self, msg):
        self.latest_costmap = msg

    def on_action_status(self, msg):
        if not msg.status_list:
            return
        status = msg.status_list[-1].status
        if status == self.last_status:
            return
        self.last_status = status

        if status == GOAL_STATUS_SUCCEEDED:
            self.get_logger().info('Goal reached -> spinning in place')
            self._enter_spinning()
        elif status in (GOAL_STATUS_ACCEPTED, GOAL_STATUS_EXECUTING):
            if self.state != STATE_IDLE:
                self.get_logger().info(
                    f'New goal started while {self.state} -> stopping and '
                    'handing /cmd_vel back to controller_server')
            self._enter_idle()

    def on_diffusion_complete(self, _msg):
        if self.state != STATE_SPINNING:
            return
        self.get_logger().info('diffusion_complete received -> stop spinning')
        self.current_angular_z = 0.0
        self.cmd_pub.publish(Twist())

        self.target_yaw = self._compute_away_from_wall_yaw()
        if self.target_yaw is None:
            self.get_logger().warn(
                'Could not find a nearby wall (no costmap/pose data yet or '
                'nothing within wall_search_radius) -- skipping orientation, '
                'holding in place instead')
            self._enter_holding()
        else:
            self.get_logger().info(
                f'Turning to face away from nearest wall (target_yaw='
                f'{self.target_yaw:.2f} rad)')
            self.state = STATE_ORIENTING

    # -- state transitions ---------------------------------------------

    def _enter_spinning(self):
        self._cancel_hold_timer()
        self.state = STATE_SPINNING
        self.current_angular_z = 0.0

    def _enter_holding(self):
        self.state = STATE_HOLDING
        self.current_angular_z = 0.0
        self.cmd_pub.publish(Twist())
        self._cancel_hold_timer()
        # create_timer repeats by default; cancel it on its own first firing
        # so it behaves as a one-shot hold timer.
        self.hold_timer = self.create_timer(self.hold_duration, self._on_hold_done)

    def _enter_idle(self):
        self._cancel_hold_timer()
        self.state = STATE_IDLE
        self.current_angular_z = 0.0
        self.target_yaw = None

    def _on_hold_done(self):
        self.get_logger().info(f'{self.hold_duration}s hold complete')
        self._enter_idle()

    def _cancel_hold_timer(self):
        if self.hold_timer is not None:
            self.hold_timer.cancel()
            self.hold_timer = None

    # -- nearest-wall lookup ---------------------------------------------

    def _compute_away_from_wall_yaw(self):
        grid = self.latest_costmap
        pose = self.robot_pose
        if grid is None or pose is None:
            return None

        rx, ry, _ = pose
        res = grid.info.resolution
        ox = grid.info.origin.position.x
        oy = grid.info.origin.position.y
        width = grid.info.width
        height = grid.info.height
        data = grid.data

        robot_col = int((rx - ox) / res)
        robot_row = int((ry - oy) / res)
        cell_radius = max(1, int(self.wall_search_radius / res))

        col_lo = max(0, robot_col - cell_radius)
        col_hi = min(width - 1, robot_col + cell_radius)
        row_lo = max(0, robot_row - cell_radius)
        row_hi = min(height - 1, robot_row + cell_radius)

        best_dist_sq = None
        best_wx = best_wy = None
        for row in range(row_lo, row_hi + 1):
            row_base = row * width
            for col in range(col_lo, col_hi + 1):
                if data[row_base + col] < self.wall_cost_threshold:
                    continue
                wx = ox + (col + 0.5) * res
                wy = oy + (row + 0.5) * res
                dist_sq = (wx - rx) ** 2 + (wy - ry) ** 2
                if best_dist_sq is None or dist_sq < best_dist_sq:
                    best_dist_sq = dist_sq
                    best_wx, best_wy = wx, wy

        if best_dist_sq is None:
            return None

        bearing_to_wall = math.atan2(best_wy - ry, best_wx - rx)
        return normalize_angle(bearing_to_wall + math.pi)

    # -- command loop ------------------------------------------------------

    def _ramp_toward(self, target):
        step = self.angular_accel / CMD_PUBLISH_RATE
        if self.current_angular_z < target:
            self.current_angular_z = min(self.current_angular_z + step, target)
        else:
            self.current_angular_z = max(self.current_angular_z - step, target)

    def on_cmd_timer(self):
        if self.state == STATE_SPINNING:
            self._ramp_toward(self.spin_angular_z)
        elif self.state == STATE_ORIENTING:
            if self.robot_pose is None or self.target_yaw is None:
                return
            yaw_err = normalize_angle(self.target_yaw - self.robot_pose[2])
            if abs(yaw_err) <= self.orient_yaw_tolerance:
                self.get_logger().info('Facing away from nearest wall')
                self._enter_holding()
                return
            target_speed = math.copysign(self.orient_angular_speed, yaw_err)
            self._ramp_toward(target_speed)
        else:
            return

        twist = Twist()
        twist.angular.z = self.current_angular_z
        self.cmd_pub.publish(twist)


def main():
    rclpy.init(args=sys.argv)
    node = PostGoalSpinNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
