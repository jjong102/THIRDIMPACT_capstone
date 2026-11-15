"""Stationary 2-D scan-to-map initialization. No velocity commands or ROS code.

A previous pose is only a candidate; whole-map alternatives are always searched.
Ambiguous or incomplete searches never authorize initialization.
"""
import hashlib
import math
import time
import numpy as np
from scipy.ndimage import binary_dilation, distance_transform_edt
from .core import transform, relative, register_scan, wrap, voxel


class GridModel:
    def __init__(self, cells, resolution, origin):
        self.cells = np.asarray(cells, dtype=np.int16)
        self.resolution = float(resolution)
        self.origin = np.asarray(origin, dtype=float)
        self.height, self.width = self.cells.shape
        self.free = self.cells == 0
        self.occupied = self.cells >= 65
        self.clearance = distance_transform_edt(np.pad(self.free, 1))[1:-1, 1:-1]*self.resolution

        boundary = self.occupied & binary_dilation(self.free)
        self.distance = distance_transform_edt(~boundary)*self.resolution
        self.distance_gradient = np.gradient(self.distance, self.resolution)
        row, col = np.nonzero(boundary)
        self.reference = np.column_stack(((col+.5)*self.resolution, (row+.5)*self.resolution))
        self.fingerprint = hashlib.sha256(self.cells.tobytes()+repr((resolution, list(origin))).encode()).hexdigest()

    def world_pose(self, local):
        xy = transform(np.asarray([local[:2]]), self.origin)[0]
        return np.array([*xy, wrap(local[2]+self.origin[2])])

    def local_pose(self, world):
        return relative(self.origin, world)

    def indices(self, points):
        ij = np.floor(np.asarray(points)/self.resolution).astype(int)
        good = (ij[..., 0]>=0) & (ij[..., 0]<self.width) & (ij[..., 1]>=0) & (ij[..., 1]<self.height)
        return np.clip(ij[..., 1], 0, self.height-1), np.clip(ij[..., 0], 0, self.width-1), good

    def scores(self, points, poses):
        """Robust endpoint cost; unknown/outside endpoints are penalized."""
        poses = np.asarray(poses)
        c, s = np.cos(poses[:, 2, None]), np.sin(poses[:, 2, None])
        x = c*points[None, :, 0]-s*points[None, :, 1]+poses[:, 0, None]
        y = s*points[None, :, 0]+c*points[None, :, 1]+poses[:, 1, None]
        row, col, good = self.indices(np.stack((x, y), axis=-1))
        distances = np.minimum(self.distance[row, col], .4)
        distances[~good | (self.cells[row, col]<0)] = .4
        # Retain 85%; a few moving people should not determine the pose.
        k = max(1, int(points.shape[0]*.85))
        return np.partition(distances, k-1, axis=1)[:, :k].mean(axis=1)

    def footprint_clear(self, pose):
        # Cover the full URDF envelope, including protrusions and interior cells.
        x = np.linspace(-.35, .35, int(np.ceil(.70/(self.resolution*.5)))+1)
        y = np.linspace(-.22, .22, int(np.ceil(.44/(self.resolution*.5)))+1)
        xx, yy = np.meshgrid(x, y)
        boundary = np.column_stack((xx.ravel(), yy.ravel()))
        row, col, good = self.indices(transform(boundary, pose))
        return bool(np.all(good & self.free[row, col]))

    def ray_fraction(self, points, pose, origin=None):
        # Wrong hypotheses often match endpoints through intervening walls.
        origin = np.zeros(2) if origin is None else np.asarray(origin)
        samples = np.vstack([origin+(points-origin)*f for f in (.25, .5, .75)])
        row, col, good = self.indices(transform(samples, pose))
        return float(np.mean(good & self.free[row, col])) if len(samples) else 0.

    def rays_clear(self, points, pose):
        return self.ray_fraction(points, pose) >= .90

    def verify(self, points, world_pose, origin=None, min_overlap=.70):
        """Local geometric consistency, not a guarantee of global uniqueness."""
        if len(points) == 0:
            return {'valid': False, 'overlap': 0., 'score': .4,
                    'footprint_clear': False, 'rays_free_fraction': 0.,
                    'failures': ['유효 LiDAR 관측 없음'], 'reason': '유효 LiDAR 관측 없음'}
        pose = self.local_pose(world_pose)
        moved = transform(points, pose)
        row, col, good = self.indices(moved)
        distances = np.where(good, self.distance[row, col], 1.)
        overlap = float(np.mean(good & (self.cells[row, col]>=0) & (distances<.12)))
        score = float(self.scores(points, np.array([pose]))[0])
        footprint = self.footprint_clear(pose)
        rays = self.ray_fraction(points, pose, origin)
        failures = []
        if overlap < min_overlap: failures.append(f'벽 경계 일치율 {overlap*100:.1f}% < {min_overlap*100:.0f}%')
        if score > .065: failures.append(f'벽 경계 오차 {score*100:.1f}cm > 6.5cm')
        if not footprint: failures.append('70×44cm 차체 외곽 공간 부족')
        if rays < .90: failures.append(f'광선 자유 공간 {rays*100:.1f}% < 90%')
        return {'valid': not failures, 'overlap': overlap, 'min_overlap': min_overlap, 'score': score,
                'footprint_clear': footprint, 'rays_free_fraction': rays,
                'failures': failures, 'reason': ' / '.join(failures) or '위치 검증 통과'}


def distinct(poses, max_count, distance=.4, angle=math.radians(18)):
    chosen = []
    for pose in poses:
        if all(np.linalg.norm(pose[:2]-p[:2])>distance or abs(wrap(pose[2]-p[2]))>angle for p in chosen):
            chosen.append(pose)
            if len(chosen) >= max_count:
                break
    return chosen


def search_map(model, points, previous_pose=None, timeout=20., cancel=None, verifier=None):
    started = time.monotonic()
    def interrupted():
        return (cancel is not None and cancel.is_set()) or time.monotonic()-started>timeout
    def failure(reason):
        return {'confident': False, 'reason': reason, 'elapsed': time.monotonic()-started}
    points = voxel(points, .025)
    if len(points)<80 or len(model.reference)<80:
        return failure('insufficient_geometry')
    sampled = points[np.linspace(0, len(points)-1, min(180, len(points))).astype(int)]
    step = max(1, round(.25/model.resolution))
    rows, cols = np.mgrid[0:model.height:step, 0:model.width:step]
    valid = model.clearance[rows, cols] >= .22
    xy = np.column_stack(((cols[valid]+.5)*model.resolution, (rows[valid]+.5)*model.resolution))
    if not len(xy):
        return failure('no_free_candidates')
    angles = np.arange(-math.pi, math.pi, math.radians(15))
    poses = np.column_stack((np.repeat(xy, len(angles), axis=0), np.tile(angles, len(xy))))
    if len(poses)>400000:
        return failure('map_too_large')
    scores = []
    for i in range(0, len(poses), 128):
        if interrupted(): return failure('search_interrupted')
        scores.extend(model.scores(sampled, poses[i:i+128]))
    order = np.argsort(scores)
    seeds = distinct(poses[order], 32)
    if previous_pose is not None:
        seeds.append(model.local_pose(previous_pose))
    fine = []
    offsets = np.linspace(-step*model.resolution*.75, step*model.resolution*.75, 5)
    turns = np.linspace(-math.radians(9), math.radians(9), 7)
    shifts = np.array([[x, y, a] for x in offsets for y in offsets for a in turns])
    for seed in seeds:
        if interrupted(): return failure('search_interrupted')
        candidates = seed+shifts
        costs = model.scores(sampled, candidates)
        fine.append((float(costs.min()), candidates[int(np.argmin(costs))]))
    fine.sort(key=lambda x:x[0])
    refined = []
    current = points[np.linspace(0,len(points)-1,min(700,len(points))).astype(int)]
    for seed in distinct([p for _,p in fine], 24, .25, math.radians(10)):
        if interrupted(): return failure('search_interrupted')
        result = register_scan(current, model.reference, seed, max_pair=.3)
        if result is None or result['overlap']<.65 or result['observability']<.01 or result['condition']>1000:
            continue
        local = result['pose']
        world = model.world_pose(local)
        verification = verifier(world) if verifier is not None else model.verify(current, world)
        if not verification['valid']:
            continue
        if any(np.linalg.norm(local[:2]-r['local'][:2])<.25 and abs(wrap(local[2]-r['local'][2]))<math.radians(10) for r in refined):
            continue
        refined.append({'pose':world, 'local':local, **verification})
    if not refined:
        return failure('no_consistent_pose')
    refined.sort(key=lambda r:r['score'])
    best = refined[0]
    runner = refined[1]['score'] if len(refined)>1 else None
    # Do not seed an equally plausible rotated/translated copy of the room.
    confident = runner is None or (runner-best['score']>max(.01, model.resolution*.25)
                                   and runner>best['score']*1.5)
    return {'confident':confident, 'reason':'unique_match' if confident else 'ambiguous',
            'pose':best['pose'], 'score':best['score'], 'overlap':best['overlap'],
            'runner_score':runner, 'candidates':len(refined),
            'alternatives':[{'pose':r['pose'].tolist(),'score':r['score'],'overlap':r['overlap']} for r in refined[:4]],
            'elapsed':time.monotonic()-started}


def verify_sensors(model, cloud, sensors, origins, pose):
    """Per-sensor overlap gates; retain combined cost, footprint and ray gates."""
    quality = dict(model.verify(cloud, pose, min_overlap=0.))
    failures = list(quality.get('failures', []))
    if not quality['valid'] and not failures:
        failures.append(quality['reason'])
    quality['sensors'] = {}
    for key, threshold in (('front', .70), ('rear', .70)):
        result = model.verify(sensors[key], pose, origins[key], min_overlap=threshold)
        quality['sensors'][key] = result
        if result.get('overlap', 0.) < threshold:
            failures.append(f"{key} 벽 경계 일치율 {result.get('overlap', 0.)*100:.1f}% < {threshold*100:.0f}%")
    quality.update(valid=not failures, failures=failures,
                   reason=' / '.join(failures) or '위치 검증 통과')
    return quality


def verify_tracking(model, sensors, origins, pose):
    """Occlusion-aware verification of an already established pose only.

    No scan is changed: these masks are private to localization, never collision
    checking. Unknown space or a return behind a mapped wall is not occlusion.
    """
    local = model.local_pose(pose)
    evidence = []; normals = []; details = {}; total = occluded = contradictions = 0
    grad_y, grad_x = model.distance_gradient
    for key, points in sensors.items():
        points = voxel(np.asarray(points), .04)
        origin = np.asarray(origins[key])
        vectors = points-origin
        lengths = np.linalg.norm(vectors, axis=1)
        directions = vectors/np.maximum(lengths[:, None], 1e-6)
        # Ray march to the first occupied/unknown cell, including the space
        # before the measured endpoint. The scan's actual sensor origin matters.
        steps = np.arange(0., 6.01, model.resolution*.5)
        rays = origin+directions[:, None, :]*steps[None, :, None]
        c, s = math.cos(local[2]), math.sin(local[2])
        moved = np.stack((c*rays[..., 0]-s*rays[..., 1]+local[0],
                          s*rays[..., 0]+c*rays[..., 1]+local[1]), axis=-1)
        row, col, inside = model.indices(moved)
        known_free = inside & model.free[row, col]
        stops = ~known_free
        first = np.argmax(stops, axis=1)
        has_stop = stops.any(axis=1)
        expected = steps[first]
        idx = np.arange(len(points))
        wall = has_stop & inside[idx, first] & model.occupied[row[idx, first], col[idx, first]]
        endpoints = transform(points, local)
        er, ec, good = model.indices(endpoints)
        hit = good & (model.distance[er, ec] < .12) & wall & (np.abs(lengths-expected) < .18)
        hidden = wall & (expected-lengths > .20) & good & model.free[er, ec] & ~hit
        bad = ~(hit | hidden)
        total += len(points); occluded += int(hidden.sum()); contradictions += int(bad.sum())
        evidence.extend(endpoints[hit])
        # Surface normals sampled just in front of the observed mapped wall.
        probes = transform(points[hit]-directions[hit]*.10, local)
        pr, pc, _ = model.indices(probes)
        normals.extend(np.column_stack((grad_x[pr, pc], grad_y[pr, pc])))
        details[key] = dict(returns=len(points), matched=int(hit.sum()),
                            occluded=int(hidden.sum()), contradictory=int(bad.sum()),
                            overlap=float(hit.mean()) if len(hit) else 0.)
    evidence = np.asarray(evidence).reshape(-1, 2)
    normals = np.asarray(normals).reshape(-1, 2)
    information = 0.
    if len(evidence) >= 40:
        magnitude = np.linalg.norm(normals, axis=1)
        usable = magnitude > .3
        n = normals[usable]/magnitude[usable, None]
        p = evidence[usable]-local[:2]
        if len(p) >= 40:
            jac = np.column_stack((n, -n[:, 0]*p[:, 1]+n[:, 1]*p[:, 0]))
            # Scale rotation by scene extent so long rays cannot hide a weak
            # translation axis. A single flat wall has rank < 3 and must stop.
            jac[:, 2] /= max(1., float(np.sqrt(np.mean(np.sum(p*p, axis=1)))))
            information = float(np.linalg.eigvalsh(jac.T@jac/len(jac))[0])
    footprint = model.footprint_clear(local)
    contradictory_fraction = contradictions/max(total, 1)
    geometry = len(evidence) >= 40 and information >= .015
    inconsistent = not footprint or contradictory_fraction > .20
    valid = geometry and not inconsistent
    reason = ('가림 제외 후 지도 구조로 위치 추적 중' if valid else
              '관측과 지도 위치 모순' if inconsistent else '가림으로 위치 관측 부족 · 정지하여 관측 회복 대기')
    return dict(valid=valid, reason=reason, sensors=details, matched=len(evidence),
                occluded=occluded, contradictory_fraction=contradictory_fraction,
                information=information, footprint_clear=footprint,
                insufficient=not geometry and not inconsistent,
                overlap=len(evidence)/max(total, 1), failures=[] if valid else [reason])
