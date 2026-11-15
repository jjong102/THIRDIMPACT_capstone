"""Full mission: forward-first return, pre-entry rotation, reverse docking.

Return motion policy is owned by impact_node (config/impact.yaml):
forward blockage waits 5 seconds then replans; reverse blockage only waits.
"""
from third_impact.bringup import generate
from launch.actions import DeclareLaunchArgument
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node

def generate_launch_description():
    description = generate('mission')
    description.add_action(DeclareLaunchArgument('log', default_value='false'))
    description.add_action(DeclareLaunchArgument(
        'log_dir', default_value='/home/moonshot/capstone_final_ws/src/third_impact/debug_log'))
    description.add_action(Node(
        package='third_impact', executable='debug_recorder', output='screen',
        condition=IfCondition(LaunchConfiguration('log')),
        parameters=[{'log_dir': LaunchConfiguration('log_dir')}]))
    return description
