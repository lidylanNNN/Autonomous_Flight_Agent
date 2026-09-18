#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PX4_ROOT="${PX4_ROOT:-${HOME}/PX4-Autopilot}"
AGENT_BIN="${XRCE_AGENT_BIN:-${HOME}/.local/bin/MicroXRCEAgent}"
XRCE_PORT="${XRCE_PORT:-8888}"

[[ -x "${AGENT_BIN}" ]] || { echo "MicroXRCEAgent not found: ${AGENT_BIN}" >&2; exit 1; }
[[ -d "${PX4_ROOT}" ]] || { echo "PX4 source not found: ${PX4_ROOT}" >&2; exit 1; }

cleanup() {
  [[ -n "${PX4_PID:-}" ]] && kill "${PX4_PID}" 2>/dev/null || true
  [[ -n "${AGENT_PID:-}" ]] && kill "${AGENT_PID}" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

LD_LIBRARY_PATH="${HOME}/.local/lib:${LD_LIBRARY_PATH:-}" \
  "${AGENT_BIN}" udp4 -p "${XRCE_PORT}" &
AGENT_PID=$!
sleep 2
cd "${PX4_ROOT}"
HEADLESS=1 make px4_sitl gz_x500 &
PX4_PID=$!
echo "PX4 SITL is running; press Ctrl-C to stop."
wait "${PX4_PID}"
