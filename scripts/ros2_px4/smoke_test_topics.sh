#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
set +u
# shellcheck disable=SC1091
source /opt/ros/jazzy/setup.bash
# shellcheck disable=SC1091
source "${PROJECT_ROOT}/ros2_ws/install/setup.bash"
set -u

topics="$(ros2 topic list --no-daemon)"
required_topics=(
  /fmu/out/battery_status
  /fmu/out/home_position
  /fmu/out/vehicle_command_ack
  /fmu/out/vehicle_land_detected
  /fmu/out/vehicle_local_position
  /fmu/out/vehicle_status_v1
  /fmu/in/vehicle_command
)
for topic in "${required_topics[@]}"; do
  grep -qx "${topic}" <<<"${topics}" || { echo "FAIL missing topic: ${topic}" >&2; exit 1; }
done
echo "OK PX4 ROS 2 topic smoke test"
printf '%s\n' "${topics}" | grep -E '^/fmu/(in|out)/' | sort
