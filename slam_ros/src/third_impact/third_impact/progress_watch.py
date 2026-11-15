"""Credit commanded yaw progress, exclude command gaps, bound rotation-only credit."""
import math
import numpy as np
from .core import wrap


class ProgressWatch:
    def __init__(self):
        self.last_time = None
        self.anchor = None
        self.rotation_started = None

    def update(self, now, pose, command, fresh):
        dt = 0. if self.last_time is None else max(0., now-self.last_time)
        self.last_time = now
        if self.anchor is None or np.linalg.norm(np.asarray(pose[:2])-self.anchor[:2]) >= .2:
            self.anchor = np.asarray(pose).copy()
            self.rotation_started = None
            return 0., False
        if not fresh:
            return dt, False
        v, w = command
        if abs(v) < .03 and abs(w) > .04:
            if self.rotation_started is None:
                self.rotation_started = now
            delta = wrap(pose[2]-self.anchor[2])
            if now-self.rotation_started <= 30. and delta*w > 0 and abs(delta) >= math.radians(5):
                self.anchor[2] = pose[2]
                return 0., True
        return 0., False
