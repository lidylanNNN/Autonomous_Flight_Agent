#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
source /opt/ros/jazzy/setup.bash
source "${PROJECT_ROOT}/ros2_ws/install/setup.bash"

topics="$(ros2 topic list --no-daemon)"
for topic in /fmu/out/vehicle_status_v1 /fmu/out/vehicle_local_position /fmu/out/vehicle_command_ack /fmu/in/vehicle_command; do
  grep -qx "${topic}" <<<"${topics}" || { echo "FAIL missing topic: ${topic}" >&2; exit 1; }
done
echo "OK PX4 ROS 2 topic smoke test"
printf '%s\n' "${topics}" | grep -E '^/fmu/(in|out)/' | sort
