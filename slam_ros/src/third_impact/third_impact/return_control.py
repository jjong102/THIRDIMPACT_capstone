"""Return-only route owner. Nav2 plans; only this supervisor commands motion.

No NavigateToPose recoveries/replanning run while following a return route.
"""
import math
import time
import numpy as np
from geometry_msgs.msg import Twist
from nav_msgs.msg import Path
from nav2_msgs.action import ComputePathToPose
from rclpy.action import ActionClient
from action_msgs.msg import GoalStatus
from .core import wrap, swept_clear, transform, region_map, stationary_allowed_many, relative, alignment_command
from .return_route import FootprintMap, select_entry, docking_entries, dense_path, tracking_command, obstacle_action, braking_path
from .editor import yaw


class ReturnControl:
    def init_return(self):
        self.return_planner = ActionClient(self, ComputePathToPose, '/compute_path_to_pose')
        self.return_path_pub = self.create_publisher(Path, '/third_impact/return_path', 10)
        self.return_local_pub = self.create_publisher(Path, '/third_impact/return_local_path', 10)
        self.return_epoch = 0
        self.return_future = None
        self.return_plan_handle = None
        self.return_request_time = 0.
        self.return_map_msg = None
        self.return_map = None
        self.reset_return()

    def reset_return(self):
        self.return_docking = False
        self.return_dock_attempt = 0
        self.return_reapproaches = 0
        self.return_reapproach_pending = False
        self.micro_adjustment = None
        self.micro_attempts = 0
        self.return_alignment_wait = False
        self.position_alignment = None
        self.fine_alignment_started = None
        self.coarse_alignment = None
        self.alignment_diagnostics = {}
        self.alignment_reject_since = None
        self.alignment_recheck_until = 0.
        self.alignment_valid_count = 0
        self.alignment_last_pose = None
        self.return_epoch += 1
        self.return_route = None
        self.return_phase = 'PLAN'
        self.return_index = 0
        self.return_blocked = None
        self.return_collision = None
        self.return_wait_reason = ''
        self.return_reverse = False
        self.return_entry = None
        self.return_retry = 0.
        self.return_last_v = 0.
        self.return_last_w = 0.
        self.return_last_tick = time.monotonic()
        self.return_paused_pose = None
        self.return_pause_manual = False
        self.return_failed = False

    def pause_return(self):
        self.return_pause_manual = getattr(self, 'return_pause_manual', False) or bool(self.manual)
        self.micro_adjustment = None
        handle = getattr(self, 'return_plan_handle', None)
        if handle is not None:
            handle.cancel_goal_async()
        self.return_plan_handle = None
        self.return_future = None
        self.position_alignment = None
        self.fine_alignment_started = None
        self.coarse_alignment = None
        self.align_last_key = None
        self.alignment_valid_count = 0
        # Preserve a reverse corridor across sensor loss/manual intervention.
        # Never silently replan out of a reverse manoeuvre.
        if self.return_paused_pose is None:
            self.return_paused_pose = self.pose()
        self.return_epoch += 1
        self.return_blocked = None
        self.return_last_v = self.return_last_w = 0.

    def return_grid(self):
        if self.return_map_msg is not self.grid:
            g = self.grid
            origin = g.info.origin
            self.return_map = FootprintMap(region_map(np.asarray(g.data).reshape(g.info.height, g.info.width),
                           g.info.resolution, [origin.position.x, origin.position.y, yaw(origin.orientation)],
                           self.data.get('boundary')),
                g.info.resolution, [origin.position.x, origin.position.y, yaw(origin.orientation)],
                self.p['footprint_half_length'], self.p['footprint_half_width'])
            self.return_map_msg = g
        return self.return_map

    def request_return_path(self, pose):
        now = time.monotonic()
        if self.return_future is not None:
            self.detail = '복귀 경로 계산 응답 대기 중'
            if now-self.return_request_time > 5.:
                self.return_epoch += 1
                if self.return_plan_handle is not None:
                    self.return_plan_handle.cancel_goal_async()
                self.return_future = None
                self.return_plan_handle = None
                self.return_dock_attempt += 1
                self.return_retry = now+1.
                self.detail = '복귀 경로 계산 시간 초과: 다른 진입점 검사'
            return
        if now < self.return_retry or not self.return_planner.server_is_ready():
            self.detail = '복귀 경로 계획 서버 준비 또는 재시도 대기 중'
            return
        if self.return_alignment_wait:
            self.detail = '복귀 재접근 한도 초과: 정지 유지, 목표·주변 공간 확인 필요'
            return
        entries = docking_entries(self.data['goal'], self.return_grid())
        if not entries or self.return_dock_attempt >= len(entries):
            self.return_alignment_wait = True
            self.return_wait_reason = '목표 방향으로 진입 가능한 경로 없음: 정지 유지'
            self.detail = self.return_wait_reason
            return
        entry, reverse = entries[self.return_dock_attempt]
        self.return_planned_entry = entry
        self.return_planned_reverse = reverse
        goal = ComputePathToPose.Goal()
        goal.goal = self.pose_message(entry)
        goal.start = self.pose_message(pose)
        goal.use_start = True
        goal.planner_id = 'GridBased'
        epoch = self.return_epoch
        self.return_request_time = now
        self.return_future = self.return_planner.send_goal_async(goal)
        def accepted(future):
            try:
                handle = future.result()
                if epoch != self.return_epoch:
                    if handle.accepted:
                        handle.cancel_goal_async()
                    return
                if not handle.accepted:
                    self.return_dock_attempt += 1
                    self.return_future = None
                    self.return_retry = time.monotonic()+2.
                    return
                self.return_plan_handle = handle
                self.return_future = handle.get_result_async()
                self.return_future.add_done_callback(lambda f: self.return_path_done(f, epoch))
            except Exception as exc:
                if epoch != self.return_epoch:
                    return
                self.return_dock_attempt += 1
                self.return_future = None
                self.return_retry = time.monotonic()+2.
                self.detail = '복귀 경로 요청 실패: '+str(exc)
        self.return_future.add_done_callback(accepted)

    def return_path_done(self, future, epoch):
        if epoch != self.return_epoch or self.intent != 'return':
            return
        self.return_future = None
        self.return_plan_handle = None
        try:
            result = future.result()
            if result.status != GoalStatus.STATUS_SUCCEEDED:
                raise ValueError('경로 계산 실패')
            msg = result.result.path
            if msg.header.frame_id != 'map' or len(msg.poses) < 2:
                raise ValueError('map 경로가 없거나 너무 짧음')
            points = np.array([[p.pose.position.x, p.pose.position.y] for p in msg.poses])
            if not np.isfinite(points).all() or np.linalg.norm(points[-1]-self.return_planned_entry[:2]) > .05:
                raise ValueError('경로가 저장 목표까지 연결되지 않음')
            pose = self.pose()
            if pose is None or self.manual or not self.healthy():
                raise ValueError('위치·센서 또는 조작 모드 확인 필요')
            if np.linalg.norm(points[0]-pose[:2]) > .15:
                raise ValueError('경로 요청 이후 로봇 위치가 달라짐')
            incoming = math.atan2(*(points[-1]-points[-2])[::-1])
            entry = self.return_planned_entry
            grid = self.return_grid()
            plan = (dict(reverse=self.return_planned_reverse, path=dense_path(points),
                         entry=entry, docking=True)
                    if grid.rotation(entry[:2], incoming, entry[2])
                    and grid.corridor(entry, np.asarray(self.data['goal'])) else None)
            if plan is None:
                raise ValueError('진입점 회전 또는 마지막 직선 통로 부족')
            self.install_return_plan(plan)
        except Exception as exc:
            self.return_dock_attempt += 1
            self.return_retry = time.monotonic()+2.
            self.return_failed = True
            self.detail = '복귀 경로 대기: '+str(exc)

    def install_return_plan(self, plan):
        self.return_failed = False
        if getattr(self, 'return_reapproach_pending', False):
            self.return_reapproaches += 1
            self.return_reapproach_pending = False
        self.return_route = plan['path']
        self.return_entry = plan['entry']
        self.return_reverse = plan['reverse']
        self.return_docking = plan.get('docking', False)
        self.return_index = 0
        self.return_phase = 'FORWARD'
        self.return_blocked = None
        self.event('목표 방향 진입 경로 준비: '+('진입점에서 정렬 후 후진' if self.return_reverse else '진입점에서 정렬 후 전진'))

    def show_return_path(self, publisher, points):
        msg = Path(); msg.header.frame_id = 'map'; msg.header.stamp = self.get_clock().now().to_msg()
        msg.poses = [self.pose_message([*p, self.data['goal'][2]]) for p in points]
        publisher.publish(msg)

    def return_command(self, pose, v, w, reverse):
        now = time.monotonic()
        requested_v, requested_w = v, w
        dt = min(.1, max(.001, now-self.return_last_tick))
        linear_accel = self.p.get('return_linear_accel', .35)
        angular_accel = self.p.get('return_angular_accel', .4)
        self.return_last_tick = now
        # Same acceleration and speed limits in either direction; emergency stop bypasses ramp.
        v = float(np.clip(v, self.return_last_v-linear_accel*dt, self.return_last_v+linear_accel*dt))
        w = float(np.clip(w, self.return_last_w-angular_accel*dt, self.return_last_w+angular_accel*dt))
        cloud = self.cloud()
        measured = self.odom.twist.twist
        actual = (float(measured.linear.x), float(measured.angular.z))
        grid = self.return_grid()
        remaining = (float(np.linalg.norm(self.return_route[-1]-pose[:2]))
                     if self.return_route is not None else None)
        self.return_collision = dict(updated_at=time.time(), requested=[requested_v,requested_w],
            measured=list(actual), remaining=remaining, reason='clear', rejected=[])

        clear_cache = {}
        def check(vv, ww, source):
            key = (float(vv), float(ww))
            if key in clear_cache:
                return True, clear_cache[key]
            local = braking_path(vv, ww, linear_decel=min(.5, linear_accel),
                                 angular_decel=min(.8, angular_accel))
            samples = np.column_stack((transform(local[:,1:3],pose),pose[2]+local[:,3]))
            if getattr(self, 'data', {}).get('boundary') is not None:
                boundary_ok = stationary_allowed_many(samples,self.data.get('boundary'),
                    None,self.p['footprint_half_length'],self.p['footprint_half_width'])
            else:  # Minimal test doubles; production always has saved region data.
                boundary_ok = np.asarray([self.motion_allowed(p) for p in samples])
            map_ok = (grid.clear_many(samples) if hasattr(grid,'clear_many')
                      else np.asarray([grid.clear(p) for p in samples]))
            lidar_ok = np.zeros(len(local),dtype=bool)
            if cloud is not None:
                margin = self.p['return_obstacle_margin']+.01
                # Bound temporary arrays independently of scan density.
                for start in range(0,len(local),16):
                    batch = local[start:start+16]
                    dx = cloud[None,:,0]-batch[:,1,None]
                    dy = cloud[None,:,1]-batch[:,2,None]
                    c, sn = np.cos(batch[:,3,None]), np.sin(batch[:,3,None])
                    hits = ((abs(dx*c+dy*sn)<self.p['footprint_half_length']+margin) &
                            (abs(-dx*sn+dy*c)<self.p['footprint_half_width']+margin))
                    lidar_ok[start:start+len(batch)] = ~hits.any(axis=1)
            bad = np.flatnonzero(~(boundary_ok & map_ok & lidar_ok))
            if len(bad):
                i = int(bad[0])
                reason = ('sensor_missing' if cloud is None else 'boundary' if not boundary_ok[i]
                          else 'lidar' if not lidar_ok[i] else 'map')
                self.return_collision.update(reason=reason,source=source,
                    collision_time=float(local[i,0]),collision_pose=samples[i].tolist())
                return False, samples
            clear_cache[key] = samples
            return True, samples

        # A low requested speed cannot erase the stopping distance of a fast robot.
        clear, samples = check(*actual, 'actual_braking')
        if clear:
            clear = False
            for scale in ((1., .5, .25) if not reverse else (1.,)):
                candidate_v = requested_v*scale
                out_v = float(np.clip(candidate_v, self.return_last_v-linear_accel*dt,
                                      self.return_last_v+linear_accel*dt))
                # Test both requested and ramped commands to prevent creeping.
                valid, candidate_samples = check(candidate_v, requested_w, 'requested_braking')
                if valid:
                    valid, _ = check(out_v, w, 'output_braking')
                if valid:
                    v = out_v; samples = candidate_samples; clear = True
                    for key in ('source', 'collision_time', 'collision_pose'):
                        self.return_collision.pop(key, None)
                    self.return_collision.update(reason='clear', selected=[v,w], scale=scale)
                    if scale < 1.:
                        self.detail = '복귀 장애물 여유에 맞춰 저속 접근 중'
                    break
                self.return_collision['rejected'].append(dict(
                    speed=candidate_v, reason=self.return_collision['reason'],
                    source=self.return_collision['source'],
                    collision_time=self.return_collision['collision_time'],
                    collision_pose=self.return_collision['collision_pose']))
        if not clear:
            self.stop(); self.return_last_v = self.return_last_w = 0.
            self.return_blocked = self.return_blocked or now
            labels = {'lidar':'LiDAR 장애물', 'map':'지도 벽 충돌 예상',
                      'boundary':'한계 구간', 'sensor_missing':'센서 부족'}
            reason = labels[self.return_collision['reason']]
            self.return_wait_reason = reason + (': 후진 정지·해소 대기' if reverse else ': 정지 후 5초 대기')
            self.detail = self.return_wait_reason
            if (getattr(self, 'return_docking', False)
                    and self.return_phase in ('FINAL', 'REVERSE', 'ROTATE')
                    and now-self.return_blocked >= 5.):
                self.reapproach_return('마지막 진입 통로 차단: '+reason)
                return
            if obstacle_action(reverse, now-self.return_blocked) == 'replan':
                self.return_route = None; self.return_phase = 'PLAN'
                self.return_blocked = None
                self.return_epoch += 1
                self.event('전진 경로가 5초 이상 막힘: 우회 경로 계산')
            return
        self.return_blocked = None; self.return_wait_reason = ''
        self.return_last_v, self.return_last_w = v, w
        command = Twist(); command.linear.x = v; command.angular.z = w
        self.velocity_pub.publish(command)
        self.show_return_path(self.return_local_pub, np.asarray(samples)[:, :2])

    def reapproach_return(self, reason):
        """Queue one actual staging manoeuvre; never count repeated requests."""
        if getattr(self, 'return_reapproach_pending', False):
            return
        self.micro_adjustment = None
        self.stop()
        self.return_last_v = self.return_last_w = 0.
        self.return_epoch += 1
        self.return_collision = None
        self.coarse_alignment = None
        self.position_alignment = None
        self.fine_alignment_started = None
        self.alignment_valid_count = 0
        self.alignment_last_pose = None
        self.align_last_key = None
        self.align_velocity = (0., 0.)
        self.alignment_reject_since = None
        self.alignment_recheck_until = 0.
        self.settled_since = None
        self.match = None
        self.return_wait_reason = reason
        self.alignment_diagnostics = dict(phase='REAPPROACH', reason=reason,
                                          attempts=self.return_reapproaches)
        if self.return_reapproaches >= 3:
            self.return_alignment_wait = True
            self.return_wait_reason = '복귀 재접근 3회 소진: ' + reason
            self.state = 'RETURN_WAIT'
        else:
            self.return_reapproach_pending = True
            self.return_phase = 'REAPPROACH_PLAN'
            self.return_route = None
            self.return_dock_attempt = 0
            self.return_retry = time.monotonic()+1.
            self.state = 'RETURN'
        self.event(self.return_wait_reason)

    def start_micro_adjustment(self, pose, error):
        """Freeze a locally corrected map goal from three consistent ICP frames.

        map_T_goal = map_T_robot * inverse(goal_T_robot). The saved goal/reference
        is never overwritten; completion still uses fresh ICP against that reference.
        """
        if getattr(self, 'micro_attempts', 0) >= 2 or np.linalg.norm(error[:2]) > .08:
            self.reapproach_return('작은 위치 보정 범위 또는 횟수 초과')
            return
        heading = wrap(pose[2]-error[2])
        offset = transform(np.asarray([error[:2]]), [0.,0.,heading])[0]
        target = np.array([*(pose[:2]-offset), heading])
        grid = self.return_grid()
        selected = None
        for side in (-1., 1.):
            staging_xy = transform(np.array([[side*.16,0.]]), target)[0]
            staging = np.array([*staging_xy,heading])
            if grid.corridor([*pose[:2],heading], staging) and grid.corridor(staging,target):
                selected = staging, side
                break
        if selected is None:
            self.reapproach_return('작은 위치 보정 통로 부족')
            return
        now = time.monotonic()
        self.micro_attempts = getattr(self, 'micro_attempts', 0)+1
        self.micro_adjustment = dict(target=target, staging=selected[0], side=selected[1],
            phase='OUT', started=now, progress=now, best=math.inf,
            origin=np.asarray(pose).copy())
        self.stop(); self.return_last_v = self.return_last_w = 0.
        self.settled_since = None
        self.detail = '정밀 정합 기준 횡방향 오차: 짧은 전·후진 보정 준비'

    def tick_micro_adjustment(self, pose):
        job = getattr(self, 'micro_adjustment', None)
        if job is None:
            return False
        now = time.monotonic()
        target = job['target']
        error = relative(target, pose)
        self.alignment_diagnostics = dict(phase='MICRO_'+job['phase'],
            longitudinal_m=float(error[0]), lateral_m=float(error[1]),
            heading_error_deg=math.degrees(error[2]), updated_at=time.time(),
            attempts=self.micro_attempts, reference='frozen_icp_goal')
        if (now-job['started'] > 45. or np.linalg.norm(pose[:2]-job['origin'][:2]) > .35
                or abs(error[2])>.6):
            self.reapproach_return('짧은 위치 보정의 시간·이동·방향 한도 초과')
            return True
        if job['phase'].startswith('SETTLE'):
            self.return_command(pose,0.,0.,True)
            if self.stopped():
                if job['phase']=='SETTLE_OUT':
                    job.update(phase='IN',best=math.inf,progress=now)
                else:
                    self.micro_adjustment = None
                    self.position_alignment = dict(done=True)
                    self.coarse_alignment = None
                    self.fine_alignment_started = None
                    self.alignment_valid_count = 0
                    self.alignment_last_pose = None
                    self.align_last_key = None
                    self.match = None
                    self.detail = '짧은 위치 보정 완료: 저장 스캔으로 도착 조건 재검증'
            elif now-job['progress'] > 6.:
                self.reapproach_return('짧은 위치 보정 정지 확인 시간 초과')
            return True
        destination = job['staging'] if job['phase']=='OUT' else target
        reverse = (job['side']<0) if job['phase']=='OUT' else (job['side']>0)
        delta = relative([pose[0],pose[1],pose[2]+(math.pi if reverse else 0.)], destination)
        distance = float(np.linalg.norm(delta[:2]))
        if distance < .01:
            job.update(phase='SETTLE_'+job['phase'],progress=now)
            self.return_command(pose,0.,0.,True)
            return True
        if distance < job['best']-.003:
            job.update(best=distance,progress=now)
        if now-job['progress']>6.:
            self.reapproach_return('짧은 위치 보정 진행 정체 또는 통로 차단')
            return True
        angle = wrap(math.atan2(delta[1],delta[0]))
        speed = min(self.p['max_align_v'],.6*distance)*max(0.,math.cos(angle))**2
        w = float(np.clip(.8*angle,-self.p['max_align_w'],self.p['max_align_w']))
        self.detail = '정밀 정합 기준 짧은 전·후진 위치 보정 중'
        self.return_command(pose,-speed if reverse else speed,w,True)
        return True

    def position_return(self, pose):
        """Resolve lateral position using healthy map pose before final yaw/ICP.

        Differential drive cannot translate sideways. This bounded manoeuvre may
        turn while its scan differs from the saved goal scan; live map/LiDAR
        collision checks and the normal localization readiness gate still apply.
        """
        now = time.monotonic()
        error = relative(self.data['goal'], pose)
        distance = float(np.linalg.norm(error[:2]))
        stage = self.position_alignment
        if stage is None:
            stage = dict(done=False, started=now, progress=now, pose=np.asarray(pose).copy())
            self.position_alignment = stage
        if stage['done']:
            return False
        self.alignment_diagnostics = dict(phase='POSITION', longitudinal_m=float(error[0]),
            lateral_m=float(error[1]), heading_error_deg=math.degrees(error[2]),
            updated_at=time.time(), attempts=self.return_reapproaches)
        if distance <= self.p['xy_tolerance']*.9:
            self.stop(); self.return_last_v = self.return_last_w = 0.
            if self.stopped():
                stage['done'] = True
                self.coarse_alignment = None
            return True
        previous = stage['pose']
        if (np.linalg.norm(pose[:2]-previous[:2])>.005
                or abs(wrap(pose[2]-previous[2]))>.04):
            stage.update(progress=now, pose=np.asarray(pose).copy())
        if now-stage['started'] > 60. or now-stage['progress'] > 6.:
            self.reapproach_return('마지막 위치 보정 진행 정체 또는 시간 초과')
            return True
        v, w = alignment_command(error, self.p['xy_tolerance']*.9,
            math.radians(self.p['yaw_tolerance_deg']), self.p['max_align_v'], self.p['max_align_w'])
        self.detail = f'위치 우선 보정: 종방향 {error[0]*100:.1f}cm, 횡방향 {error[1]*100:.1f}cm'
        # True keeps collision stops stationary instead of implicit route replans.
        self.return_command(pose, v, w, True)
        return True

    def coarse_align(self, pose):
        """Large yaw correction precedes ICP. Never translates or relaxes ICP gates."""
        now = time.monotonic()
        error = wrap(self.data['goal'][2]-pose[2])
        stage = self.coarse_alignment
        if stage is None:
            self.match = None
            stage = dict(phase='STOP',started=now,best=abs(error),progress=now,
                         deadline=now+abs(error)/.2+8.,sign=1 if error>=0 else -1)
            self.coarse_alignment = stage
        phase = stage['phase']
        if phase != 'DONE':
            offset = relative(self.data['goal'], pose)
            self.alignment_diagnostics = dict(phase='COARSE_'+phase,
                heading_error_deg=math.degrees(error), longitudinal_m=float(offset[0]),
                lateral_m=float(offset[1]), updated_at=time.time())
        if phase == 'DONE':
            if abs(error) > .20:
                self.coarse_alignment = None
                self.alignment_valid_count = 0
                self.align_last_key = None
                self.stop()
                return True
            return False
        self.align_velocity = (0.,0.); self.settled_since = None
        if phase == 'WAIT':
            self.reapproach_return(stage['reason']); return True
        if phase in ('STOP','SETTLE'):
            self.stop()
            if not self.stopped():
                if now-stage['started']>6.:
                    stage.update(phase='WAIT',reason='큰 방향 정렬: 실제 정지 확인 시간 초과')
                return True
            if abs(error) <= .10:
                stage['phase']='DONE'; self.align_last_key=None
                self.alignment_valid_count=0
                self.detail='큰 방향 정렬 완료: 새 LiDAR 관측 3회로 정밀 정합 재검증'
                return True
            cloud = self.cloud()
            if (cloud is None or not self.return_grid().rotation(pose[:2],pose[2],pose[2]+error)
                    or not swept_clear(cloud,0.,math.copysign(.2,error),
                        self.p['footprint_half_length'],self.p['footprint_half_width'],
                        self.p['obstacle_margin'],horizon=abs(error)/.2)):
                stage.update(phase='WAIT',reason='큰 방향 정렬 공간 부족: 지도·LiDAR 회전 외곽 확인 필요')
                self.detail=stage['reason']; return True
            self.return_last_v=self.return_last_w=0.
            stage.update(phase='ROTATE',best=abs(error),progress=now,
                         deadline=now+abs(error)/.2+8.,sign=1 if error>=0 else -1)
        if abs(error)<=.08 or error*stage['sign']<0:
            self.stop();stage.update(phase='SETTLE',started=now)
            self.return_last_v=self.return_last_w=0.
            return True
        if abs(error)<stage['best']-.04:
            stage.update(best=abs(error),progress=now)
        if now>stage['deadline'] or now-stage['progress']>5.:
            self.stop();stage.update(phase='WAIT',reason='큰 방향 정렬 진행 정체: 위치·구동 상태 확인 필요')
            self.detail=stage['reason']; return True
        self.detail=f'큰 방향 정렬: 제자리 회전 중, 방향 오차 {math.degrees(error):.1f}°'
        # Return collision guard includes actual braking, map, boundary and live scan.
        self.return_command(pose,0.,float(np.clip(error,-.2,.2)),True)
        return True

    def tick_return(self, pose):
        self.state = 'RETURN'
        if self.nav_pending or self.nav_handle is not None:
            self.cancel_nav(); self.stop(); return
        if self.return_paused_pose is not None:
            if (self.return_phase in ('REVERSE', 'ROTATE') and
                    (np.linalg.norm(pose[:2]-self.return_paused_pose[:2]) > .1 or
                     abs(wrap(pose[2]-self.return_paused_pose[2])) > .2)):
                self.stop(); self.detail = '후진 진입 중 수동 위치 변경: 기존 경로 이탈, 정지 유지'; return
            if self.return_phase not in ('REVERSE', 'ROTATE', 'ALIGN'):
                route = self.return_route
                resume = (not getattr(self, 'return_pause_manual', False)
                          and self.return_phase == 'FORWARD' and route is not None
                          and np.linalg.norm(pose[:2]-self.return_paused_pose[:2]) <= .1
                          and abs(wrap(pose[2]-self.return_paused_pose[2])) <= .2
                          and np.min(np.linalg.norm(route-pose[:2],axis=1)) <= .1)
                if not resume:
                    self.return_route = None; self.return_phase = 'PLAN'
                # Resumed commands still pass the live map, boundary and LiDAR guard.
            self.return_paused_pose = None
            self.return_pause_manual = False
        goal = np.asarray(self.data['goal'])
        if self.return_alignment_wait:
            self.stop(); self.state = 'RETURN_WAIT'; self.detail = self.return_wait_reason
            return
        if self.return_phase == 'ALIGN':
            self.state = 'ALIGN'; self.align(); return
        if self.return_phase in ('PLAN', 'REAPPROACH_PLAN'):
            self.stop()
            if (self.return_phase == 'PLAN' and not self.return_reapproach_pending
                    and np.linalg.norm(pose[:2]-goal[:2]) <= .06 and abs(wrap(pose[2]-goal[2])) <= .15):
                self.return_phase = 'ALIGN'; self.align_last_key = None; return
            self.request_return_path(pose); return
        if self.return_phase == 'NO_ENTRY':
            self.stop()
            if time.monotonic() >= self.return_retry:
                self.return_retry = time.monotonic()+2.
                plan = select_entry(self.return_candidate, goal, self.return_grid(), pose[2])
                if plan is not None:
                    self.install_return_plan(plan)
            return
        display_route = self.return_route
        if (self.return_reverse or self.return_docking) and self.return_phase in ('FORWARD', 'ROTATE'):
            display_route = np.vstack((display_route, goal[:2]))
        self.show_return_path(self.return_path_pub, display_route)
        if self.return_phase == 'ROTATE':
            self.state = 'RETURN_ROTATE'
            error = wrap(goal[2]-pose[2])
            now = time.monotonic()
            progress = getattr(self, 'entry_rotation_progress', None)
            if progress is None:
                progress = dict(best=abs(error), time=now, deadline=now+abs(error)/.2+10.)
                self.entry_rotation_progress = progress
            if abs(error) < progress['best']-.04:
                progress.update(best=abs(error), time=now)
            if now > progress['deadline'] or now-progress['time'] > 6.:
                self.reapproach_return('진입점 방향 정렬 진행 정체 또는 시간 초과')
                return
            if abs(error) < .04 and self.stopped():
                self.return_route = dense_path([pose[:2], goal[:2]])
                self.return_index = 0; self.return_phase = 'REVERSE' if self.return_reverse else 'FINAL'
                self.event('방향 정렬 완료: '+('후진' if self.return_reverse else '전진')+' 진입 시작'); return
            self.detail = '좁은 구간 진입 전 목표 방향으로 정렬 중'
            self.return_command(pose, 0., 0. if abs(error)<.04 else np.clip(error, -.35, .35), True)
            return
        reverse = self.return_phase == 'REVERSE'
        self.state = 'RETURN_REVERSE' if reverse else 'RETURN_FORWARD'
        endpoint = goal[:2] if reverse or self.return_phase == 'FINAL' else self.return_route[-1]
        distance = np.linalg.norm(pose[:2]-endpoint)
        if distance < (.015 if reverse or self.return_phase == 'FINAL' else .035):
            self.return_command(pose,0.,0.,True)
            if not self.stopped():
                return
            if self.return_phase == 'FORWARD' and (self.return_reverse or self.return_docking):
                self.return_phase = 'ROTATE'
                self.entry_rotation_progress = None
            else:
                self.return_phase = 'ALIGN'; self.align_last_key = None
            return
        # Lost tracking in a reverse corridor never causes an escape/replan.
        if reverse and (abs(wrap(pose[2]-goal[2])) > .35 or
                        np.min(np.linalg.norm(self.return_route-pose[:2], axis=1)) > .10):
            self.stop(); self.detail = '후진 경로 또는 방향 이탈: 우회 없이 정지'; return
        self.detail = '후진으로 목표 접근 중' if reverse else '기존 글로벌 경로를 따라 전진 중'
        v, w, self.return_index = tracking_command(pose, self.return_route, self.return_index,
            reverse, self.p['return_max_speed'], self.p['return_max_angular_speed'],
            measured_speed=abs(self.odom.twist.twist.linear.x))
        # Approach speed uses geometric endpoint distance, not only path length.
        # Decelerate before the endpoint, then hand off to position/yaw alignment.
        room = max(0., distance-(.005 if reverse or self.return_phase == 'FINAL' else .015))
        approach_speed = min(self.p['return_max_speed'], .7*room, math.sqrt(2*.5*room))
        v = math.copysign(min(abs(v),approach_speed),v)
        self.return_command(pose, v, w, reverse)
