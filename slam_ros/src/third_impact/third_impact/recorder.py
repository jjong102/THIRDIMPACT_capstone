"""Observation only: local rosbag plus diagnostic events and configuration snapshot."""
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo
import yaml
import rclpy
from rclpy.node import Node
from std_msgs.msg import String
from ament_index_python.packages import get_package_share_directory


class Recorder(Node):
    def __init__(self):
        super().__init__('third_impact_debug_recorder')
        root = Path(self.declare_parameter('log_dir', '/home/moonshot/capstone_final_ws/src/third_impact/debug_log').value).expanduser()
        stamp = datetime.now(ZoneInfo('Asia/Seoul')).strftime('%Y%m%d_%H%M%S_%f')
        self.folder = root/stamp; self.folder.mkdir(parents=True)
        self.events = (self.folder/'diagnostics.jsonl').open('a', buffering=1)
        self.output = (self.folder/'rosbag.log').open('w')
        qos = Path(get_package_share_directory('third_impact'))/'config/bag_qos.yaml'
        command = ['ros2', 'bag', 'record', '--all', '--include-hidden-topics', '--max-bag-size', '1073741824',
                   '--qos-profile-overrides-path', str(qos), '-o', str(self.folder/'bag')]
        self.process = subprocess.Popen(command, stdout=self.output, stderr=subprocess.STDOUT, start_new_session=True)
        self.reported_exit = False
        for topic in ('/third_impact/status', '/third_impact/events', '/third_impact/oscillation', '/third_impact/drive_debug', '/third_impact/amcl_scan_status', '/third_impact/localization'):
            self.create_subscription(String, topic, lambda msg, t=topic: self.write(t, msg.data), 100)
        self.snapshot_stop = threading.Event()
        self.worker = threading.Thread(target=self.snapshot, daemon=True); self.worker.start()
        self.create_timer(5., self.check)
        self.get_logger().info(f'Recording on Jetson: {self.folder}')

    def write(self, topic, data):
        self.events.write(json.dumps({'received_at': time.time(), 'topic': topic, 'data': data})+'\n')

    def snapshot(self):
        share = Path(get_package_share_directory('third_impact'))
        shutil.copytree(share/'config', self.folder/'config')
        metadata = {'created_at': time.time(), 'timezone': 'Asia/Seoul', 'host': os.uname().nodename,
                    'ROS_DOMAIN_ID': os.environ.get('ROS_DOMAIN_ID'), 'RMW_IMPLEMENTATION': os.environ.get('RMW_IMPLEMENTATION')}
        (self.folder/'session.json').write_text(json.dumps(metadata, indent=2))
        commands = [('nodes.txt', ['ros2', 'node', 'list']), ('topics.txt', ['ros2', 'topic', 'list', '-t'])]
        for node in ('third_impact', 'third_impact_localization', 'amcl', 'controller_server', 'planner_server', 'velocity_smoother', 'ekf_filter_node', 'test_node'):
            commands.append((node+'.yaml', ['ros2', 'param', 'dump', '/'+node]))
        for filename, command in commands:
            if self.snapshot_stop.is_set():
                break
            try:
                result = subprocess.run(command, capture_output=True, text=True, timeout=10)
                (self.folder/filename).write_text(result.stdout+'\n'+('# '+result.stderr.replace('\n', '\n# ') if result.stderr else ''))
                if filename == 'third_impact.yaml' and result.returncode == 0:
                    doc = yaml.safe_load(result.stdout)
                    params = next(iter(doc.values()))['ros__parameters']
                    for key in ('state_file', 'map_file'):
                        path = Path(params[key]).expanduser()
                        if path.is_file():
                            shutil.copy2(path, self.folder/path.name)
                            if key == 'map_file':
                                image = path.parent/yaml.safe_load(path.read_text())['image']
                                if image.is_file():
                                    shutil.copy2(image, self.folder/image.name)
            except Exception as exc:
                (self.folder/(filename+'.error')).write_text(str(exc))

    def check(self):
        if self.process.poll() is not None:
            if not self.reported_exit:
                self.get_logger().error('Bag recorder exited; see rosbag.log. Robot mission is independent.')
                self.reported_exit = True
        elif shutil.disk_usage(self.folder).free < 2*1024**3:
            self.get_logger().error('Less than 2 GiB free; closing bag without changing robot mission')
            self.close_bag()

    def close_bag(self):
        if self.process.poll() is None:
            os.killpg(self.process.pid, signal.SIGINT)
            try:
                self.process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                os.killpg(self.process.pid, signal.SIGTERM)
                self.process.wait(timeout=5)

    def close(self):
        self.snapshot_stop.set()
        self.close_bag()
        self.worker.join(timeout=11)
        self.events.close(); self.output.close()


def main(args=None):
    rclpy.init(args=args)
    node = Recorder()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.close(); node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
