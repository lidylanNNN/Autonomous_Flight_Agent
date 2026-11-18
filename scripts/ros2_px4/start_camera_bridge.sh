#!/usr/bin/env bash
set -euo pipefail

if [[ $# -eq 1 && ( "$1" == "--help" || "$1" == "-h" ) ]]; then
  echo "Usage: $0 (default world, x500_mono_cam instance 0; start simulation separately)"
  exit 0
elif [[ $# -ne 0 ]]; then
  echo "Usage: $0 (no arguments)" >&2
  exit 2
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
set +u
# shellcheck disable=SC1091
source /opt/ros/jazzy/setup.bash
set -u

for package in ros_gz_bridge sensor_msgs rosgraph_msgs; do
  ros2 pkg prefix "${package}" >/dev/null || {
    echo "Missing ROS package: ${package}" >&2
    exit 1
  }
done

echo "Bridging Gazebo default/x500_mono_cam_0 to /camera/image_raw, /camera/camera_info, /clock"
echo "Bridge startup does not imply valid images; verify actual messages."
exec ros2 run ros_gz_bridge parameter_bridge --ros-args \
  -p "config_file:=${SCRIPT_DIR}/camera_bridge.yaml"
