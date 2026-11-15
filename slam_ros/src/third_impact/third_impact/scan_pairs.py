"""Small timestamp-ordered scan history; source timestamps bound freshness."""
from collections import deque
from threading import Lock


class ScanPairs:
    def __init__(self):
        self.history = {'front': deque(maxlen=8), 'rear': deque(maxlen=8)}
        self.lock = Lock()

    def add(self, key, stamp, sample):
        with self.lock:
            self.history[key].append((stamp, sample))

    def select(self, now, timeout):
        with self.lock:
            front, rear = (list(self.history[k]) for k in ('front','rear'))
        pairs = [(min(a[0],b[0]), -abs(a[0]-b[0]), a, b)
                 for a in front for b in rear
                 if 0 <= now-a[0] <= timeout and 0 <= now-b[0] <= timeout
                 and abs(a[0]-b[0]) <= .15]
        if not pairs:
            return None
        _, _, a, b = max(pairs, key=lambda p:p[:2])
        return {'front':a[1], 'rear':b[1]}
