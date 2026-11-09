#!/usr/bin/env bash
set -eo pipefail

if [[ -f /opt/ros/humble/setup.bash ]]; then
  set +u
  source /opt/ros/humble/setup.bash
  set -u
else
  echo "ROS 2 Humble이 없습니다" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-3}"
export ROS_LOCALHOST_ONLY=0
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
export CYCLONEDDS_URI="${CYCLONEDDS_URI:-file://$HOME/.ros/cyclonedds.xml}"

echo "[receiver] DOMAIN=$ROS_DOMAIN_ID RMW=$RMW_IMPLEMENTATION"
echo "[receiver] CYCLONEDDS_URI=$CYCLONEDDS_URI"
exec python3 -u "$SCRIPT_DIR/jetson_receiver.py"
