"""Collision checked forward-first return geometry (no ROS or hardware)."""
import math
import numpy as np
from .core import transform, relative, wrap


class FootprintMap:
    def __init__(self, cells, resolution, origin, half_length=.35, half_width=.22):
        self.cells = np.asarray(cells)
        self.resolution = resolution
        self.origin = origin
        # Cover the interior, not only corners; unknown and outside are occupied.
        step = resolution / 2
        x = np.linspace(-half_length, half_length, math.ceil(2*half_length/step)+1)
        y = np.linspace(-half_width, half_width, math.ceil(2*half_width/step)+1)
        self.body = np.array(np.meshgrid(x, y)).reshape(2, -1).T

    def clear(self, pose):
        p = transform(self.body, relative(self.origin, pose))
        indices = np.floor(p/self.resolution).astype(int)
        x, y = indices.T
        h, w = self.cells.shape
        return bool(np.all((x >= 0) & (y >= 0) & (x < w) & (y < h)) and
                    np.all(self.cells[y, x] == 0))

    def clear_many(self, poses):
        """Same body samples as clear(), batched in bounded chunks."""
        poses = np.asarray(poses)
        output = []
        c0, s0 = math.cos(self.origin[2]), math.sin(self.origin[2])
        origin_rotation = np.array([[c0,-s0],[s0,c0]])
        h, w = self.cells.shape
        for start in range(0,len(poses),16):
            batch = poses[start:start+16]
            angles = batch[:,2]-self.origin[2]
            centers = (batch[:,:2]-self.origin[:2]) @ origin_rotation
            c, s = np.cos(angles)[:,None], np.sin(angles)[:,None]
            x = self.body[:,0]*c-self.body[:,1]*s+centers[:,0,None]
            y = self.body[:,0]*s+self.body[:,1]*c+centers[:,1,None]
            ix, iy = np.floor(x/self.resolution).astype(int), np.floor(y/self.resolution).astype(int)
            valid = ((ix>=0)&(iy>=0)&(ix<w)&(iy<h)).all(axis=1)
            valid &= (self.cells[np.clip(iy,0,h-1),np.clip(ix,0,w-1)]==0).all(axis=1)
            output.extend(valid)
        return np.asarray(output,dtype=bool)

    def rotation(self, position, start, end):
        delta = wrap(end-start)
        return all(self.clear([*position, start+t*delta])
                   for t in np.linspace(0, 1, max(2, math.ceil(abs(delta)/.025)+1)))

    def corridor(self, start, goal):
        length = np.linalg.norm(np.asarray(start[:2])-goal[:2])
        return all(self.clear([*(np.asarray(start[:2])*(1-t)+np.asarray(goal[:2])*t), goal[2]])
                   for t in np.linspace(0, 1, max(2, math.ceil(length/(self.resolution/2))+1)))


def dense_path(points, spacing=.04):
    output = [np.asarray(points[0], dtype=float)]
    for a, b in zip(points[:-1], points[1:]):
        a, b = np.asarray(a), np.asarray(b)
        n = max(1, math.ceil(np.linalg.norm(b-a)/spacing))
        output.extend(a+(b-a)*i/n for i in range(1, n+1))
    return np.asarray(output)


def select_entry(points, goal, grid, current_yaw):
    """Stay on the supplied route. Choose its latest feasible straight-reverse entry."""
    points = dense_path(points)
    if len(points) < 2:
        return None
    incoming = math.atan2(*(points[-1]-points[-2])[::-1])
    if grid.rotation(goal[:2], incoming, goal[2]):
        return dict(reverse=False, path=points, entry=np.asarray(goal))
    # Search backwards: maximize forward progress, never invent a detour.
    for i in range(len(points)-1, -1, -1):
        local = relative(goal, [*points[i], goal[2]])
        if local[0] < .4 or abs(local[1]) > .06:
            continue
        heading = current_yaw if i == 0 else math.atan2(*(points[i]-points[i-1])[::-1])
        # Project at most 6 cm onto the guaranteed straight goal entrance.
        entry = transform(np.array([[local[0], 0.]]), goal)[0]
        if (grid.rotation(points[i], heading, goal[2]) and
                grid.rotation(entry, heading, goal[2]) and
                grid.corridor([*entry, goal[2]], goal)):
            path = np.vstack((points[:i+1], entry))
            return dict(reverse=True, path=dense_path(path), entry=np.array([*entry, goal[2]]))
    return None



def docking_entries(goal, grid):
    """Goal-axis staging poses; forward first, then reverse, no unsafe corridor."""
    goal = np.asarray(goal)
    entries = []
    for distance in (-.5, .5, -.7, .7):
        xy = transform(np.array([[distance, 0.]]), goal)[0]
        entry = np.array([*xy, goal[2]])
        if grid.clear(entry) and grid.corridor(entry, goal):
            entries.append((entry, distance > 0))
    return entries

def tracking_command(pose, path, index, reverse, max_v=.4, max_w=.35, measured_speed=0.):
    """Curvature tracking with speed-adaptive preview, symmetric in reverse."""
    end = min(len(path), index+12)
    index += int(np.argmin(np.linalg.norm(path[index:end]-pose[:2], axis=1)))
    while index < len(path)-1 and np.linalg.norm(path[index]-pose[:2]) < .08:
        index += 1
    preview = float(np.clip(.45+1.5*abs(measured_speed), .45, .9))
    target_index = index
    distance = np.linalg.norm(path[index]-pose[:2])
    while target_index < len(path)-1 and distance < preview:
        distance += np.linalg.norm(path[target_index+1]-path[target_index])
        target_index += 1
    travel_pose = [pose[0], pose[1], pose[2]+(math.pi if reverse else 0.)]
    target = relative(travel_pose, [*path[target_index], travel_pose[2]])
    error = wrap(math.atan2(target[1], target[0]))
    remaining = np.linalg.norm(path[index]-pose[:2])+np.linalg.norm(np.diff(path[index:],axis=0),axis=1).sum()
    speed = min(max_v, math.sqrt(max(0.,2*.35*remaining)),remaining*1.2)
    # Smoothly slow on bends. Large initial errors use low-gain in-place turning.
    speed *= max(0., math.cos(1.5*min(abs(error),math.pi/3)))**2
    if abs(error) > math.pi/3:
        return 0., float(np.clip(.7*error,-max_w,max_w)), index
    curvature = 2.*target[1]/max(float(target[0]**2+target[1]**2), .04**2)
    if abs(curvature)>1e-9:
        speed = min(speed,max_w/abs(curvature))
    turn_blend = float(np.clip((abs(error)-math.pi/4)/(math.pi/12),0.,1.))
    angular = (1.-turn_blend)*speed*curvature + turn_blend*.7*error
    return (-speed if reverse else speed), float(np.clip(angular,-max_w,max_w)), index


def obstacle_action(reverse, elapsed):
    return 'wait' if reverse or elapsed < 5. else 'replan'


def braking_path(v, w, delay=.2, linear_decel=.5, angular_decel=.8, dt=.02):
    """Local body poses through reaction delay and full stop, including time.

    Integrate conservatively with the velocity at the beginning of each step.
    Never truncate at the destination: real stopping space is still required.
    """
    x = y = angle = t = 0.
    rows = [[t, x, y, angle]]
    duration = delay + max(abs(v)/linear_decel, abs(w)/angular_decel)
    while t < duration-1e-9:
        step = min(dt, duration-t)
        elapsed = max(0., t-delay)
        vv = math.copysign(max(0., abs(v)-linear_decel*elapsed), v)
        ww = math.copysign(max(0., abs(w)-angular_decel*elapsed), w)
        x += vv*math.cos(angle+ww*step/2)*step
        y += vv*math.sin(angle+ww*step/2)*step
        angle += ww*step
        t += step
        rows.append([t,x,y,angle])
    return np.asarray(rows)
