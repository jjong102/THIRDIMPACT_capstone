#!/usr/bin/env bash
set -eo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

if [[ -f /opt/ros/humble/setup.bash ]]; then
  set +u
  # shellcheck disable=SC1091
  source /opt/ros/humble/setup.bash
  set -u
fi

export ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-3}"
export ROS_LOCALHOST_ONLY="${ROS_LOCALHOST_ONLY:-0}"
export RMW_IMPLEMENTATION="${RMW_IMPLEMENTATION:-rmw_cyclonedds_cpp}"
export CYCLONEDDS_URI="${CYCLONEDDS_URI:-file://$HOME/.ros/cyclonedds.xml}"
export ROS_NAV_BRIDGE_PORT="${ROS_NAV_BRIDGE_PORT:-5179}"

exec python3 -u "$ROOT/server/ros_nav_bridge.py"
