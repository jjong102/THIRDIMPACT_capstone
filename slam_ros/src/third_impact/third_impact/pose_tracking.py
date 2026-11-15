"""Bounded odometry bridge for brief TF lookup failures; never refresh from a bridge."""
import math
import numpy as np
from .core import relative, transform, wrap


class PoseBridge:
    def __init__(self):
        self.anchor = None

    def update(self, pose, odom, stamp):
        self.anchor = (np.asarray(pose).copy(), np.asarray(odom).copy(), stamp)

    def estimate(self, odom, stamp, now, max_age=.2):
        if self.anchor is None:
            return None, 'no_anchor'
        pose, origin, start = self.anchor
        if not 0. <= now-start <= max_age or not start <= stamp <= now:
            return None, 'anchor_expired_or_clock_jump'
        if now-stamp > .1:
            return None, 'odom_stale'
        delta = relative(origin, odom)
        if not np.isfinite(delta).all() or np.linalg.norm(delta[:2]) > .12 or abs(delta[2]) > .12:
            return None, 'odom_jump'
        xy = transform(np.asarray([delta[:2]]), pose)[0]
        return np.array([*xy, wrap(pose[2]+delta[2])]), 'odom_bridge'
