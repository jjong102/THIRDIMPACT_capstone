"""Reachable patrol goals and cancellable, bounded Nav2 planning preflight."""
import math
import time
import numpy as np
from scipy.ndimage import label
from nav2_msgs.action import ComputePathToPose
from action_msgs.msg import GoalStatus
from .core import relative
from .editor import yaw


def costmap_component(msg, pose):
    """Known traversable cells only; 253/254 and unknown 255 are not targets."""
    info = msg.metadata
    cells = np.asarray(msg.data).reshape(info.size_y, info.size_x)
    origin = [info.origin.position.x, info.origin.position.y, yaw(info.origin.orientation)]
    p = relative(origin, pose)
    x, y = math.floor(p[0]/info.resolution), math.floor(p[1]/info.resolution)
    connected, _ = label(cells < 253)  # Four-connected, no diagonal corner cutting.
    if not (0 <= x < info.size_x and 0 <= y < info.size_y) or connected[y, x] == 0:
        return np.zeros(cells.shape, dtype=bool)
    return connected == connected[y, x]


class PatrolPlanning:
    def init_patrol_planning(self):
        self.patrol_costmap = None
        self.patrol_costmap_stamp = 0.
        self.patrol_plan_job = None
        self.patrol_rejected = []

    def on_patrol_costmap(self, msg):
        if msg.header.frame_id != 'map':
            return
        self.patrol_costmap = msg
        self.patrol_costmap_stamp = time.monotonic()

    def reachable_patrol_target(self, target):
        msg = self.patrol_costmap
        pose = self.pose()
        if msg is None or pose is None or time.monotonic()-self.patrol_costmap_stamp > 2.:
            return False
        info = msg.metadata
        p = relative([info.origin.position.x, info.origin.position.y,
                      yaw(info.origin.orientation)], [*target[:2], 0.])
        x, y = math.floor(p[0]/info.resolution), math.floor(p[1]/info.resolution)
        if not (0 <= x < info.size_x and 0 <= y < info.size_y):
            return False
        return bool(costmap_component(msg, pose)[y, x])

    def reject_patrol_target(self, target, reason):
        now = time.monotonic()
        self.patrol_rejected = [(p, end) for p, end in self.patrol_rejected if end > now]
        self.patrol_rejected.append((list(target[:2]), now+30.))
        self.patrol_retry_target = None
        self.patrol_recovery = None
        self.next_attempt = now+.2
        self.state = 'PATROL_SEARCH'
        self.stop()
        self.event(reason + ' — 다른 도달 가능한 정찰 목표 탐색 중')

    def cancel_patrol_plan(self):
        job = getattr(self, 'patrol_plan_job', None)
        self.patrol_plan_job = None
        if job is not None and job['handle'] is not None:
            job['handle'].cancel_goal_async()

    def patrol_plan_waiting(self):
        job = self.patrol_plan_job
        if job is None:
            return False
        self.state = 'PATROL_SEARCH'
        if time.monotonic()-job['started'] > 5.:
            self.cancel_patrol_plan()
            self.next_attempt = time.monotonic()+self.p['retry_seconds']
            self.detail = '경로 검사 응답 시간 초과: 계획 서버 재확인 대기'
        return True

    def request_patrol_plan(self, target):
        if self.patrol_plan_job is not None:
            return
        self.state = 'PATROL_SEARCH'
        if not self.reachable_patrol_target(target):
            self.reject_patrol_target(target, '현재 비용 지도에서 목표가 이동 가능 영역과 연결되지 않음')
            return
        if not self.patrol_planner.server_is_ready():
            self.next_attempt = time.monotonic()+self.p['retry_seconds']
            self.detail = '정찰 경로 검사 서버 준비 대기'
            return
        job = dict(target=list(target), started=time.monotonic(), handle=None,
                   map_key=self.navigation_map_key)
        self.patrol_plan_job = job
        request = ComputePathToPose.Goal()
        request.goal = self.pose_message(target)
        request.planner_id = 'GridBased'
        request.use_start = False
        self.detail = '정찰 후보까지 실제 경로 생성 가능 여부 확인 중'

        def accepted(future):
            try:
                handle = future.result()
                if self.patrol_plan_job is not job:
                    if handle.accepted:
                        handle.cancel_goal_async()
                    return
                if not handle.accepted:
                    self.patrol_plan_job = None
                    self.next_attempt = time.monotonic()+self.p['retry_seconds']
                    self.detail = '경로 검사 요청 거절: 계획 서버 재확인 대기'
                    return
                job['handle'] = handle
                handle.get_result_async().add_done_callback(finished)
            except Exception as exc:
                unavailable(exc)

        def unavailable(exc):
            if self.patrol_plan_job is job:
                self.patrol_plan_job = None
                self.next_attempt = time.monotonic()+self.p['retry_seconds']
                self.detail = '경로 검사 통신 실패: '+str(exc)

        def finished(future):
            if self.patrol_plan_job is not job:
                return
            try:
                result = future.result()
            except Exception as exc:
                unavailable(exc)
                return
            self.patrol_plan_job = None
            # Late results must never restart motion after manual/return/zone changes.
            if (self.intent != 'patrol' or self.manual or not self.healthy()
                    or self.navigation_map_key != job['map_key']
                    or self.nav_pending or self.nav_handle is not None):
                return
            path = result.result.path
            valid = result.status == GoalStatus.STATUS_SUCCEEDED and path.header.frame_id == 'map' and len(path.poses) >= 2
            if valid:
                end = path.poses[-1].pose.position
                valid = math.hypot(end.x-target[0], end.y-target[1]) <= self.p['patrol_tolerance']
            if not valid:
                self.reject_patrol_target(target, '선택한 목표까지 유효한 경로 없음')
            elif not self.reachable_patrol_target(target):
                self.reject_patrol_target(target, '경로 검사 중 비용 지도 변경: 목표 재선택')
            else:
                self.send_nav(target, 'patrol')

        try:
            self.patrol_planner.send_goal_async(request).add_done_callback(accepted)
        except Exception as exc:
            unavailable(exc)
