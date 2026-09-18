#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VENV_PYTHON="${PROJECT_ROOT}/.venv/bin/python"

[[ -x "${VENV_PYTHON}" ]] || {
  echo "Project virtual environment not found: ${VENV_PYTHON}" >&2
  exit 1
}
[[ -f "${PROJECT_ROOT}/ros2_ws/install/setup.bash" ]] || {
  echo "ROS 2 workspace is not built; missing ros2_ws/install/setup.bash" >&2
  exit 1
}

set +u
# shellcheck disable=SC1091
source /opt/ros/jazzy/setup.bash
# shellcheck disable=SC1091
source "${PROJECT_ROOT}/ros2_ws/install/setup.bash"
set -u

venv_site_packages="$(
  "${VENV_PYTHON}" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])'
)"
export PYTHONPATH="${venv_site_packages}:${PROJECT_ROOT}/src:${PYTHONPATH:-}"

exec "${PROJECT_ROOT}/ros2_ws/install/flight_agent_ros/lib/flight_agent_ros/world_state_node"
