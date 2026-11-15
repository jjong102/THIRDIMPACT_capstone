"""Laptop RViz with the workspace's explicit CycloneDDS network settings."""
from pathlib import Path

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, LogInfo, OpaqueFunction, SetEnvironmentVariable
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def start(context):
    teaching = LaunchConfiguration('teach').perform(context).lower() == 'true'
    domain = LaunchConfiguration('domain_id').perform(context)
    if not domain.isdecimal() or not 0 <= int(domain) <= 232:
        raise RuntimeError('domain_id must be an integer from 0 to 232')
    dds_config = Path(LaunchConfiguration('dds_config').perform(context)).expanduser().resolve()
    if not dds_config.is_file():
        raise RuntimeError(
            f'Laptop DDS configuration not found: {dds_config}. '
            'Keep your working laptop.xml there or pass dds_config:=/absolute/path/laptop.xml')
    config = Path(get_package_share_directory('third_impact'))/'rviz'/(
        'teach.rviz' if teaching else 'debug.rviz')
    return [
        SetEnvironmentVariable('ROS_DOMAIN_ID', domain),
        SetEnvironmentVariable('ROS_LOCALHOST_ONLY', '0'),
        SetEnvironmentVariable('RMW_IMPLEMENTATION', 'rmw_cyclonedds_cpp'),
        SetEnvironmentVariable('CYCLONEDDS_URI', dds_config.as_uri()),
        LogInfo(msg=f'Laptop RViz: ROS_DOMAIN_ID={domain}, CycloneDDS={dds_config}'),
        Node(package='rviz2', executable='rviz2', arguments=['-d', str(config)], output='screen'),
    ]


def generate_launch_description():
    return LaunchDescription([
        DeclareLaunchArgument('teach', default_value='true'),
        DeclareLaunchArgument('domain_id', default_value='3'),
        DeclareLaunchArgument('dds_config', default_value=str(Path.home()/'remote_rviz/laptop.xml')),
        OpaqueFunction(function=start),
    ])
