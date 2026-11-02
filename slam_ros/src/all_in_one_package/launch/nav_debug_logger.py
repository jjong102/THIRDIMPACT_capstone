#!/usr/bin/env python3
"""Nav2 goal-checker debug logger.

Watches robot pose, goal pose, cmd_vel (pre/post velocity_smoother), and the
navigate_to_pose action's status/feedback, and writes one CSV row per event
to <log_dir>/nav_debug_<timestamp>.csv. It also prints a live summary line
and a loud [TOLERANCE MET] marker the moment the robot pose first satisfies
the controller_server goal_checker's xy/yaw tolerance -- that tells us
whether the robot ever actually reaches the tolerance band it is supposedly
failing to stop inside of.
"""
import csv
import math
import os
import sys
import time
from datetime import datetime

import rclpy
from rclpy.node import Node

from action_msgs.msg import GoalStatusArray
from geometry_msgs.msg import PoseStamped, PoseWithCovarianceStamped, Twist
from nav2_msgs.action import NavigateToPose
from nav_msgs.msg import Odometry
from std_msgs.msg import String

DEFAULT_LOG_DIR = '/home/moonshot/third_impact_capstone/src/log'

# Keep these in sync with controller_server.goal_checker in nav2_params.yaml
XY_GOAL_TOLERANCE = 0.16
YAW_GOAL_TOLERANCE = 0.10
# Slightly larger than goal_checker xy so /xy_arrived fires once we are at
# the spot, before DWB RotateToGoal starts the final in-place yaw align.
# controller_server FollowPath.xy_goal_tolerance is 0.25.
XY_ARRIVE_TOLERANCE = 0.30
# AMCL is "localized" only with a recent pose and small covariance.
LOC_POSE_STALE_SEC = 2.0
LOC_XY_VAR_MAX = 0.50      # cov[0] + cov[7]
LOC_YAW_VAR_MAX = 0.25     # cov[35]

GOAL_STATUS_NAMES = {
    0: 'UNKNOWN', 1: 'ACCEPTED', 2: 'EXECUTING',
    3: 'CANCELING', 4: 'SUCCEEDED', 5: 'CANCELED', 6: 'ABORTED',
}


def yaw_from_quat(q):
    siny_cosp = 2.0 * (q.w * q.z + q.x * q.y)
    cosy_cosp = 1.0 - 2.0 * (q.y * q.y + q.z * q.z)
    return math.atan2(siny_cosp, cosy_cosp)


def angle_diff(a, b):
    d = a - b
    while d > math.pi:
        d -= 2.0 * math.pi
    while d < -math.pi:
        d += 2.0 * math.pi
    return d


class NavDebugLogger(Node):
    def __init__(self, log_dir):
        super().__init__('nav_debug_logger')

        os.makedirs(log_dir, exist_ok=True)
        stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        self.csv_path = os.path.join(log_dir, f'nav_debug_{stamp}.csv')
        self._csv_file = open(self.csv_path, 'w', newline='')
        self._csv = csv.writer(self._csv_file)
        self._csv.writerow([
            'time', 'elapsed', 'source',
            'robot_x', 'robot_y', 'robot_yaw',
            'goal_x', 'goal_y', 'goal_yaw',
            'dist_to_goal', 'yaw_err', 'in_xy_tol', 'in_yaw_tol',
            'cmd_vel_x', 'cmd_vel_theta',
            'cmd_vel_nav_x', 'cmd_vel_nav_theta',
            'odom_vx', 'odom_vtheta',
            'action_status', 'distance_remaining', 'recoveries',
        ])
        self._t0 = time.monotonic()

        self.robot_pose = None
        self.goal_pose = None
        self.last_cmd_vel = (0.0, 0.0)
        self.last_cmd_vel_nav = (0.0, 0.0)
        self.last_odom = (0.0, 0.0)
        self.last_action_status = ''
        self.last_distance_remaining = ''
        self.last_recoveries = ''
        self._last_print = 0.0
        self._tol_met_since = None
        self._xy_arrived_sent = False
        self._last_amcl_time = None
        self._last_amcl_cov = None
        self._loc_status = None

        self.create_subscription(
            PoseWithCovarianceStamped, '/amcl_pose', self.on_amcl_pose, 10)
        self.create_subscription(
            PoseStamped, '/goal_pose', self.on_goal_pose, 10)
        self.create_subscription(Twist, '/cmd_vel', self.on_cmd_vel, 10)
        self.create_subscription(
            Twist, '/cmd_vel_nav', self.on_cmd_vel_nav, 10)
        self.create_subscription(Odometry, '/odom', self.on_odom, 10)
        self.create_subscription(
            GoalStatusArray, '/navigate_to_pose/_action/status',
            self.on_action_status, 10)
        self.create_subscription(
            NavigateToPose.Impl.FeedbackMessage,
            '/navigate_to_pose/_action/feedback',
            self.on_action_feedback, 10)

        # /goal_status (std_msgs/String) for the other Jetson:
        #   "xy_arrived" -- at goal XY, before yaw align
        #   "succeeded"  -- navigate_to_pose SUCCEEDED (xy + yaw)
        #   "executing"  -- new goal started (reset)
        self.goal_status_pub = self.create_publisher(String, '/goal_status', 10)
        # /loc_status (std_msgs/String):
        #   "not_localized" -- no/stale/high-cov AMCL pose
        #   "localized"     -- recent AMCL pose with tight covariance
        self.loc_status_pub = self.create_publisher(String, '/loc_status', 10)
        self.create_timer(1.0, self._check_loc_status)

        self.get_logger().info(f'Logging navigation debug data to {self.csv_path}')
        self.get_logger().info(
            f'Other-Jetson topic /goal_status: xy_arrived (xy<{XY_ARRIVE_TOLERANCE}m) '
            'then succeeded')
        self.get_logger().info(
            'Other-Jetson topic /loc_status: not_localized / localized')

    def elapsed(self):
        return round(time.monotonic() - self._t0, 3)

    def write_row(self, source):
        rx, ry, ryaw = self.robot_pose if self.robot_pose else ('', '', '')
        gx, gy, gyaw = self.goal_pose if self.goal_pose else ('', '', '')
        dist = yaw_err = ''
        in_xy = in_yaw = ''
        if self.robot_pose and self.goal_pose:
            dist = math.hypot(gx - rx, gy - ry)
            yaw_err = abs(angle_diff(gyaw, ryaw))
            in_xy = dist <= XY_GOAL_TOLERANCE
            in_yaw = yaw_err <= YAW_GOAL_TOLERANCE
            self._maybe_publish_xy_arrived(dist)
            now = time.monotonic()
            if in_xy and in_yaw:
                if self._tol_met_since is None:
                    self._tol_met_since = now
                    self.get_logger().warn(
                        f'[TOLERANCE MET] dist={dist:.3f} yaw_err={yaw_err:.3f} '
                        f'at t={self.elapsed()}s -- watch whether the robot '
                        f'actually stops now')
            else:
                self._tol_met_since = None

        self._csv.writerow([
            datetime.now().isoformat(), self.elapsed(), source,
            rx, ry, ryaw, gx, gy, gyaw,
            dist, yaw_err, in_xy, in_yaw,
            self.last_cmd_vel[0], self.last_cmd_vel[1],
            self.last_cmd_vel_nav[0], self.last_cmd_vel_nav[1],
            self.last_odom[0], self.last_odom[1],
            self.last_action_status, self.last_distance_remaining,
            self.last_recoveries,
        ])
        self._csv_file.flush()

        now = time.monotonic()
        if now - self._last_print > 0.5:
            self._last_print = now
            dist_s = '' if dist == '' else f'{dist:.3f}'
            yaw_s = '' if yaw_err == '' else f'{yaw_err:.3f}'
            self.get_logger().info(
                f't={self.elapsed():>7.2f}s src={source:<13} '
                f'dist={dist_s:>6} yaw_err={yaw_s:>6} '
                f'cmd_vel=({self.last_cmd_vel[0]:+.3f},{self.last_cmd_vel[1]:+.3f}) '
                f'status={self.last_action_status} '
                f'dist_remaining={self.last_distance_remaining}')

    def on_amcl_pose(self, msg):
        p = msg.pose.pose
        self.robot_pose = (p.position.x, p.position.y, yaw_from_quat(p.orientation))
        self._last_amcl_time = time.monotonic()
        cov = msg.pose.covariance
        self._last_amcl_cov = (cov[0] + cov[7], cov[35])
        self._check_loc_status()
        self.write_row('amcl_pose')

    def on_goal_pose(self, msg):
        p = msg.pose
        self.goal_pose = (p.position.x, p.position.y, yaw_from_quat(p.orientation))
        self._tol_met_since = None
        self._reset_xy_arrived()
        self.get_logger().info(
            f'New goal received: x={p.position.x:.3f} y={p.position.y:.3f}')
        self.write_row('goal_pose')

    def on_cmd_vel(self, msg):
        self.last_cmd_vel = (msg.linear.x, msg.angular.z)
        self.write_row('cmd_vel')

    def on_cmd_vel_nav(self, msg):
        self.last_cmd_vel_nav = (msg.linear.x, msg.angular.z)
        self.write_row('cmd_vel_nav')

    def on_odom(self, msg):
        self.last_odom = (msg.twist.twist.linear.x, msg.twist.twist.angular.z)

    def on_action_status(self, msg):
        if msg.status_list:
            status = msg.status_list[-1].status
            new_status = GOAL_STATUS_NAMES.get(status, str(status))
            if new_status != self.last_action_status:
                if new_status == 'SUCCEEDED':
                    self._publish_goal_status('succeeded')
                elif new_status in ('EXECUTING', 'ACCEPTED'):
                    self._reset_xy_arrived()
                    self._publish_goal_status('executing')
                elif new_status in ('CANCELED', 'ABORTED'):
                    self._reset_xy_arrived()
                    self._publish_goal_status(new_status.lower())
            self.last_action_status = new_status
            self.write_row('action_status')

    def _publish_goal_status(self, name):
        msg = String()
        msg.data = name
        self.goal_status_pub.publish(msg)
        self.get_logger().info(f'/goal_status: {name}')

    def _publish_loc_status(self, name):
        if name == self._loc_status:
            return
        self._loc_status = name
        msg = String()
        msg.data = name
        self.loc_status_pub.publish(msg)
        self.get_logger().info(f'/loc_status: {name}')

    def _check_loc_status(self):
        if self._last_amcl_time is None:
            self._publish_loc_status('not_localized')
            return
        if (time.monotonic() - self._last_amcl_time) > LOC_POSE_STALE_SEC:
            self._publish_loc_status('not_localized')
            return
        if self._last_amcl_cov is None:
            self._publish_loc_status('not_localized')
            return
        xy_var, yaw_var = self._last_amcl_cov
        if xy_var > LOC_XY_VAR_MAX or yaw_var > LOC_YAW_VAR_MAX:
            self._publish_loc_status('not_localized')
            return
        self._publish_loc_status('localized')

    def _maybe_publish_xy_arrived(self, dist):
        if self._xy_arrived_sent or dist > XY_ARRIVE_TOLERANCE:
            return
        self._xy_arrived_sent = True
        self._publish_goal_status('xy_arrived')
        self.get_logger().info(
            f'[XY ARRIVED] dist={dist:.3f}m <= {XY_ARRIVE_TOLERANCE}m '
            '-- before yaw align')

    def _reset_xy_arrived(self):
        self._xy_arrived_sent = False

    def on_action_feedback(self, msg):
        fb = msg.feedback
        self.last_distance_remaining = round(fb.distance_remaining, 4)
        self.last_recoveries = fb.number_of_recoveries
        self.write_row('action_feedback')

    def destroy_node(self):
        self._csv_file.close()
        super().destroy_node()


def main():
    log_dir = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_LOG_DIR
    rclpy.init()
    node = NavDebugLogger(log_dir)
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
