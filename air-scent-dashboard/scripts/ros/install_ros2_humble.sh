#!/usr/bin/env bash
# Install ROS 2 Humble (Ubuntu 22.04) — base + rclpy for jetson_receiver
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive

if [[ "$(. /etc/os-release && echo "$VERSION_ID")" != "22.04" ]]; then
  echo "이 스크립트는 Ubuntu 22.04용입니다." >&2
  exit 1
fi

sudo apt-get update
sudo apt-get install -y \
  curl \
  gnupg \
  lsb-release \
  software-properties-common \
  locales

sudo locale-gen en_US en_US.UTF-8
sudo update-locale LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8
export LANG=en_US.UTF-8

sudo apt-get install -y ca-certificates

# ROS 2 apt key + repo
sudo mkdir -p /usr/share/keyrings
curl -sSL https://raw.githubusercontent.com/ros/rosdistro/master/ros.key \
  | sudo gpg --dearmor --yes -o /usr/share/keyrings/ros-archive-keyring.gpg

echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/ros-archive-keyring.gpg] http://packages.ros.org/ros2/ubuntu $(. /etc/os-release && echo "$UBUNTU_CODENAME") main" \
  | sudo tee /etc/apt/sources.list.d/ros2.list > /dev/null

sudo apt-get update
sudo apt-get install -y \
  ros-humble-ros-base \
  ros-humble-rmw-cyclonedds-cpp \
  python3-rosdep \
  python3-colcon-common-extensions

if [[ ! -f /etc/ros/rosdep/sources.list.d/20-default.list ]]; then
  sudo rosdep init || true
fi
rosdep update || true

# Shell setup (idempotent)
grep -q 'source /opt/ros/humble/setup.bash' "$HOME/.bashrc" \
  || echo 'source /opt/ros/humble/setup.bash' >> "$HOME/.bashrc"
grep -q 'ROS_DOMAIN_ID=3' "$HOME/.bashrc" \
  || echo 'export ROS_DOMAIN_ID=3' >> "$HOME/.bashrc"
grep -q 'ROS_LOCALHOST_ONLY=0' "$HOME/.bashrc" \
  || echo 'export ROS_LOCALHOST_ONLY=0' >> "$HOME/.bashrc"

echo
echo "설치 완료. 새 터미널에서:"
echo "  source ~/.bashrc"
echo "  python3 ~/THIRDIMPACT_capstone/air-scent-dashboard/scripts/ros/jetson_receiver.py"
