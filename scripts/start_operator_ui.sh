#!/usr/bin/env bash
set -euo pipefail
TASK_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$TASK_ROOT"
set +u
source /opt/ros/jazzy/setup.bash
source ros2_ws/install/setup.bash
set -u
if [[ -f .env ]]; then
  set -a
  source .env
  set +a
fi
export ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-85}"
export PYTHONPATH="$PWD/.venv/lib/python3.12/site-packages:$PWD/ros2_ws/src/flight_agent_ros:$PWD/src:/usr/lib/python3/dist-packages:${PYTHONPATH:-}"
exec .venv/bin/python scripts/operator_ui/server.py "$@"
