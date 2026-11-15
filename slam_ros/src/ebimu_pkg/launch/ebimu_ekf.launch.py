import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue


def generate_launch_description():
    pkg_share = get_package_share_directory('ebimu_pkg')
    ekf_config = os.path.join(pkg_share, 'config', 'ekf.yaml')

    imu_port = LaunchConfiguration('imu_port')
    imu_baud = LaunchConfiguration('imu_baud')
    invert_yaw = LaunchConfiguration('invert_yaw')
    gyro_field_indices = LaunchConfiguration('gyro_field_indices')
    accel_field_indices = LaunchConfiguration('accel_field_indices')
    gyro_in_deg_s = LaunchConfiguration('gyro_in_deg_s')
    accel_in_g = LaunchConfiguration('accel_in_g')

    return LaunchDescription([
        DeclareLaunchArgument('imu_port', default_value='/dev/ttyUSB_IMU'),
        DeclareLaunchArgument('imu_baud', default_value='115200'),
        DeclareLaunchArgument('invert_yaw', default_value='false'),
        DeclareLaunchArgument('orientation_format', default_value='quaternion'),
        DeclareLaunchArgument('quaternion_field_indices', default_value='2,1,0,3'),

        # Actual output: qz,qy,qx,qw,gx,gy,gz,ax,ay,az,...
        # 예) *roll,pitch,yaw,gx,gy,gz,ax,ay,az  -> gyro: 3,4,5 / accel: 6,7,8
        DeclareLaunchArgument('gyro_field_indices', default_value='4, 5, 6'),
        DeclareLaunchArgument('accel_field_indices', default_value='7, 8, 9'),
        DeclareLaunchArgument('gyro_in_deg_s', default_value='true'),
        DeclareLaunchArgument('accel_in_g', default_value='true'),

        Node(
            package='ebimu_pkg',
            executable='ebimu_publisher',
            name='ebimu_publisher',
            output='screen',
            parameters=[{
                'port': imu_port,
                'baudrate': ParameterValue(imu_baud, value_type=int),

                # 기존 층수 계산 노드 호환용 raw topic
                'legacy_topic_name': 'ebimu_data',

                # EKF용 IMU frame/topic
                'imu_topic_name': '/imu/data',
                'frame_id': 'imu_link',
                #'frame_id': 'base_link',

                # IMU의 실제 장착 방향은 URDF의 imu_joint로 보정
                'use_degrees': True,
                'invert_yaw': ParameterValue(invert_yaw, value_type=bool),
                'invert_gyro_z_with_yaw': True,

                'orientation_format': LaunchConfiguration('orientation_format'),
                'quaternion_field_indices': LaunchConfiguration('quaternion_field_indices'),
                # Only used when orientation_format:=euler.
                'rpy_field_indices': '0,1,2',

                # full IMU publish용 인덱스 / 단위 설정
                'gyro_field_indices': gyro_field_indices,
                'accel_field_indices': accel_field_indices,
                'gyro_in_deg_s': ParameterValue(gyro_in_deg_s, value_type=bool),
                'accel_in_g': ParameterValue(accel_in_g, value_type=bool),

                'orientation_cov_roll_pitch': 0.03,
                'orientation_cov_yaw': 0.15,
                'angular_velocity_covariance_diag': 0.02,
                'linear_acceleration_covariance_diag': 0.20,
            }],
        ),

        Node(
            package='robot_localization',
            executable='ekf_node',
            name='ekf_filter_node',
            output='screen',
            parameters=[ekf_config],
            remappings=[('odometry/filtered', '/odom')],
        ),
    ])
