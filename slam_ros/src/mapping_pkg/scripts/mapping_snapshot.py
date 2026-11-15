#!/usr/bin/env python3
"""Read-only runtime snapshots accompanying the launch-managed mapping rosbag."""
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo


NODES = (
    '/test_node', '/joy_node', '/robot_state_publisher', '/ebimu_publisher', '/ekf_filter_node',
    '/rplidar1/sllidar_node1', '/rplidar2/sllidar_node2',
    '/rplidar1/scan_filter_chain', '/rplidar2/scan_filter_chain',
    '/cartographer_node', '/cartographer_occupancy_grid_node',
)
TOPICS = (
    '/rplidar1/scan', '/rplidar1/scan_filtered', '/rplidar2/scan',
    '/rplidar2/scan_filtered', '/imu/data', '/ebimu_data', '/wheel/odometry',
    '/odom', '/tf', '/tf_static', '/map', '/submap_list', '/diagnostics', '/joy', '/cmd_vel',
)


def capture(command, destination, stop):
    """Bound CLI waits, and reap child commands even when launch is stopping."""
    if stop.is_set():
        return
    with destination.open('w') as output:
        output.write('# ' + ' '.join(command) + '\n')
        output.flush()
        child = subprocess.Popen(command, stdout=output, stderr=subprocess.STDOUT,
                                 start_new_session=True)
        deadline = time.monotonic() + 8
        while child.poll() is None:
            if stop.wait(0.1) or time.monotonic() >= deadline:
                os.killpg(child.pid, signal.SIGTERM)
                try:
                    child.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid, signal.SIGKILL)
                    child.wait()
                output.write('\n# Interrupted or timed out\n')
                break
        output.write(f'\n# returncode: {child.returncode}\n')


def main():
    folder = Path(sys.argv[1])
    stop = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stop.set())

    def snapshots():
        # The last mapping node starts at 5 seconds; retry snapshots periodically
        # so late discovery/startup does not silently leave us without parameters.
        if stop.wait(12):
            return
        while not stop.is_set():
            stamp = datetime.now(ZoneInfo('Asia/Seoul')).strftime('%H%M%S_%f')
            target = folder / 'runtime' / stamp
            target.mkdir(parents=True)
            commands = [('nodes.txt', ['ros2', 'node', 'list']),
                        ('topics.txt', ['ros2', 'topic', 'list', '-t']),
                        ('services.txt', ['ros2', 'service', 'list', '-t'])]
            for node in NODES:
                commands.append((node.strip('/').replace('/', '_') + '.yaml',
                                 ['ros2', 'param', 'dump', node]))
            for topic in TOPICS:
                commands.append(('topic_' + topic.strip('/').replace('/', '_') + '.txt',
                                 ['ros2', 'topic', 'info', '--verbose', topic]))
            for filename, command in commands:
                if stop.is_set():
                    return
                try:
                    capture(command, target / filename, stop)
                except Exception as exc:
                    (target / (filename + '.error')).write_text(str(exc))
            if stop.wait(60):
                return

    worker = threading.Thread(target=snapshots)
    worker.start()
    warned = False
    try:
        with (folder / 'host_metrics.jsonl').open('a', buffering=1) as metrics:
            while not stop.is_set():
                free = shutil.disk_usage(folder).free
                record = {'time': datetime.now(ZoneInfo('Asia/Seoul')).isoformat(),
                          'load_average': os.getloadavg(), 'disk_free_bytes': free}
                for name in ('meminfo', 'stat', 'diskstats'):
                    path = Path('/proc') / name
                    if path.exists():
                        record[name] = path.read_text()
                metrics.write(json.dumps(record) + '\n')
                if free < 2 * 1024**3 and not warned:
                    print('[mapping log] WARNING: less than 2 GiB disk space remains.', flush=True)
                    warned = True
                stop.wait(5)
    finally:
        stop.set()
        worker.join()


if __name__ == '__main__':
    main()
