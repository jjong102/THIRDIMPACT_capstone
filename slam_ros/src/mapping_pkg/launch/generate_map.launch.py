import os
import json
import shutil
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import (DeclareLaunchArgument, ExecuteProcess, GroupAction, IncludeLaunchDescription,
                            LogInfo, OpaqueFunction, RegisterEventHandler,
                            SetEnvironmentVariable, TimerAction)
from launch.conditions import IfCondition
from launch.event_handlers import OnProcessIO, OnProcessStart, OnProcessExit
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration


def setup_mapping_network(context):
    network = LaunchConfiguration('network').perform(context)
    profiles = {'wifi': 'jetson_wifi.xml', 'jetson': 'jetson_wired.xml'}
    if network not in profiles:
        raise RuntimeError('network must be wifi or jetson')
    domain = LaunchConfiguration('domain_id').perform(context)
    if not domain.isdecimal() or not 0 <= int(domain) <= 232:
        raise RuntimeError('domain_id must be an integer from 0 to 232')
    override = LaunchConfiguration('dds_config').perform(context).strip()
    path = (Path(override) if override else
            Path(get_package_share_directory('mapping_pkg')) / 'config' / profiles[network])
    path = path.expanduser().resolve()
    if not path.is_file():
        raise RuntimeError(f'Mapping DDS configuration not found: {path}')
    return [
        SetEnvironmentVariable('ROS_DOMAIN_ID', domain),
        SetEnvironmentVariable('ROS_LOCALHOST_ONLY', '0'),
        SetEnvironmentVariable('RMW_IMPLEMENTATION', 'rmw_cyclonedds_cpp'),
        SetEnvironmentVariable('CYCLONEDDS_URI', path.as_uri()),
        LogInfo(msg=f'[mapping network] network={network}, override={bool(override)}, domain={domain}, DDS={path}'),
    ]


def setup_mapping_log(context):
    if not IfCondition(LaunchConfiguration('log')).evaluate(context):
        return []

    root = Path(LaunchConfiguration('log_dir').perform(context)).expanduser().resolve()
    stamp = datetime.now(ZoneInfo('Asia/Seoul')).strftime('%Y%m%d_%H%M%S_%f')
    folder = root / stamp
    folder.mkdir(parents=True)
    (folder / 'ros_logs').mkdir()
    (folder / 'process_logs').mkdir()
    snapshot_errors = []
    # Copy installed files: these are the files actually used by this launch.
    for package, subdirs in {
        'mapping_pkg': ['launch', 'config'],
        'amr_cartographer': ['config', 'launch'],
        'ebimu_pkg': ['config', 'launch'],
        'sllidar_ros2': ['launch'],
        'serial_test': ['params', 'serial_test.launch.py'],
        'amr': ['description'],
        'cartographer': ['configuration_files'],
    }.items():
        try:
            if package == 'cartographer':
                # Cartographer's core CMake package may have no ament index entry.
                candidates = [Path(prefix) / 'share' / package for prefix in
                              context.environment.get('AMENT_PREFIX_PATH', '').split(os.pathsep)
                              if prefix]
                share = next(path for path in candidates if path.is_dir())
            else:
                share = Path(get_package_share_directory(package))
            for subdir in subdirs:
                source = share / subdir
                target = folder / 'config' / package / subdir
                target.parent.mkdir(parents=True, exist_ok=True)
                if source.is_dir():
                    shutil.copytree(source, target)
                elif source.is_file():
                    shutil.copy2(source, target)
                else:
                    snapshot_errors.append(f'Missing: {source}')
        except Exception as exc:
            snapshot_errors.append(f'{package}: {exc}')

    custom_config = context.launch_configurations.get('cartographer_config_dir')
    if custom_config:
        try:
            shutil.copytree(Path(custom_config).expanduser(), folder / 'config' / 'cartographer_override')
        except Exception as exc:
            snapshot_errors.append(f'cartographer_config_dir: {exc}')

    qos = folder / 'bag_qos.yaml'
    qos.write_text('/tf_static:\n  history: keep_last\n  depth: 100\n'
                   '  reliability: reliable\n  durability: transient_local\n')
    bag_command = [
        'ros2', 'bag', 'record', '--all', '--include-hidden-topics',
        '--storage', 'sqlite3', '--max-bag-size', '1073741824',
        '--qos-profile-overrides-path', str(qos), '-o', str(folder / 'bag'),
    ]
    (folder / 'session.json').write_text(json.dumps({
        'created_at': datetime.now(ZoneInfo('Asia/Seoul')).isoformat(),
        'hostname': os.uname().nodename,
        'launch_arguments': dict(context.launch_configurations),
        'environment': {key: context.environment.get(key) for key in (
            'ROS_DISTRO', 'ROS_DOMAIN_ID', 'RMW_IMPLEMENTATION',
            'ROS_LOCALHOST_ONLY', 'CYCLONEDDS_URI', 'AMENT_PREFIX_PATH')},
        'bag_command': bag_command,
        'snapshot_errors': snapshot_errors,
    }, indent=2, default=str))

    def save_output(event):
        stream = 'stdout' if event.from_stdout else 'stderr'
        with (folder / 'process_logs' / f'{event.process_name}.{stream}.log').open('ab') as out:
            out.write(event.text)

    def save_event(event, _context):
        record = {'time': datetime.now(ZoneInfo('Asia/Seoul')).isoformat(),
                  'process': event.process_name, 'pid': event.pid,
                  'command': event.cmd, 'event': type(event).__name__}
        if hasattr(event, 'returncode'):
            record['returncode'] = event.returncode
        with (folder / 'process_events.jsonl').open('a') as out:
            out.write(json.dumps(record) + '\n')
        return []

    recorder = ExecuteProcess(cmd=bag_command, name='mapping_bag', output='screen',
                              sigterm_timeout='30', sigkill_timeout='10')

    def recorder_exit(event, _context):
        return [LogInfo(msg=f'[mapping log] Bag recorder stopped (code {event.returncode}). '
                        f'Check {folder}/process_logs/mapping_bag*.log and bag/metadata.yaml')]

    script = os.path.join(get_package_share_directory('mapping_pkg'),
                          'scripts', 'mapping_snapshot.py')
    return [
        LogInfo(msg=f'[mapping log] Saving mapping diagnostics to {folder}'),
        SetEnvironmentVariable('ROS_LOG_DIR', str(folder / 'ros_logs')),
        RegisterEventHandler(OnProcessIO(on_stdout=save_output, on_stderr=save_output)),
        RegisterEventHandler(OnProcessStart(on_start=save_event)),
        RegisterEventHandler(OnProcessExit(on_exit=save_event)),
        RegisterEventHandler(OnProcessExit(target_action=recorder, on_exit=recorder_exit)),
        recorder,
        ExecuteProcess(cmd=['python3', script, str(folder)],
                       name='mapping_snapshot', output='screen'),
    ]


def generate_launch_description():
    serial_launch = os.path.join(
        get_package_share_directory('serial_test'),
        'serial_test.launch.py'
    )

    imu_ekf_launch = os.path.join(
        get_package_share_directory('ebimu_pkg'),
        'launch',
        'ebimu_ekf.launch.py'
    )

    lidar_launch = os.path.join(
        get_package_share_directory('sllidar_ros2'),
        'launch',
        'sllidar_c1_2_launch.py'
    )

    cartographer_launch = os.path.join(
        get_package_share_directory('amr_cartographer'),
        'launch',
        'amr_cartographer.launch.py'
    )

    return LaunchDescription([
        DeclareLaunchArgument('use_rviz', default_value='true',
                              description='Run mapping RViz on this machine'),
        DeclareLaunchArgument('log', default_value='false',
                              description='Record mapping bag, logs and configuration snapshots'),
        DeclareLaunchArgument('log_dir',
                              default_value='/home/moonshot/capstone_final_ws/src/amr_navigator/debug_log',
                              description='Parent directory for timestamped mapping recordings'),
        DeclareLaunchArgument('domain_id', default_value='3'),
        DeclareLaunchArgument('network', default_value='wifi', choices=['wifi', 'jetson'],
                             description='wifi: laptop RViz; jetson: wired UI Jetson'),
        DeclareLaunchArgument('dds_config', default_value='',
            description='Optional explicit CycloneDDS XML; overrides the network profile'),
        OpaqueFunction(function=setup_mapping_network),
        OpaqueFunction(function=setup_mapping_log),
        # 1. serial_test 내부에서 조이스틱 + 모터/휠 odom 관련 노드 실행
        # Keep the mapping DDS profile; serial's standalone wifi mode unsets it.
        # Scope its network argument so the parent's wifi/jetson choice survives.
        GroupAction(scoped=True, actions=[
            IncludeLaunchDescription(
                PythonLaunchDescriptionSource(serial_launch),
                launch_arguments={'network': 'default'}.items(),
            ),
        ]),

        # 2. IMU publisher + robot_localization EKF 실행
        #    EKF가 /wheel/odometry + /imu/data 를 받아 /odom 생성
        TimerAction(
            period=1.0,
            actions=[
                IncludeLaunchDescription(
                    PythonLaunchDescriptionSource(imu_ekf_launch)
                )
            ],
        ),

        # 3. LiDAR 실행
        TimerAction(
            period=2.5,
            actions=[
                IncludeLaunchDescription(
                    PythonLaunchDescriptionSource(lidar_launch)
                )
            ],
        ),

        # 4. Cartographer는 EKF /odom 이 뜬 뒤 실행되도록 약간 늦게 시작
        TimerAction(
            period=5.0,
            actions=[
                IncludeLaunchDescription(
                    PythonLaunchDescriptionSource(cartographer_launch),
                    launch_arguments={
                        'use_rviz': LaunchConfiguration('use_rviz'),
                    }.items()
                )
            ],
        ),
    ])
