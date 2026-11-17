#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: $0 [--model x500|x500_mono_cam]"
}

SIM_MODEL="x500"
if [[ $# -eq 1 && ( "$1" == "--help" || "$1" == "-h" ) ]]; then
  usage
  exit 0
elif [[ $# -eq 2 && "$1" == "--model" ]]; then
  case "$2" in
    x500|x500_mono_cam) SIM_MODEL="$2" ;;
    *) echo "Unsupported simulation model: $2" >&2; usage >&2; exit 2 ;;
  esac
elif [[ $# -ne 0 ]]; then
  usage >&2
  exit 2
fi

PX4_ROOT="${PX4_ROOT:-${HOME}/PX4-Autopilot}"
AGENT_BIN="${XRCE_AGENT_BIN:-${HOME}/.local/bin/MicroXRCEAgent}"
XRCE_PORT="${XRCE_PORT:-8888}"
PX4_PARAM_BIN="${PX4_ROOT}/build/px4_sitl_default/bin/px4-param"
PX4_ROOTFS="${PX4_ROOT}/build/px4_sitl_default/rootfs"

[[ -x "${AGENT_BIN}" ]] || { echo "MicroXRCEAgent not found: ${AGENT_BIN}" >&2; exit 1; }
[[ -d "${PX4_ROOT}" ]] || { echo "PX4 source not found: ${PX4_ROOT}" >&2; exit 1; }

cleanup() {
  if [[ -n "${PX4_PID:-}" ]]; then
    kill "${PX4_PID}" 2>/dev/null || true
  fi
  if [[ -n "${AGENT_PID:-}" ]]; then
    kill "${AGENT_PID}" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

configure_headless_sitl() {
  for _ in {1..120}; do
    if (cd "${PX4_ROOTFS}" && "${PX4_PARAM_BIN}" set NAV_DLL_ACT 0) \
      >/dev/null 2>&1; then
      echo "PX4 headless profile: NAV_DLL_ACT=0"
      return
    fi
    if ! kill -0 "${PX4_PID}" 2>/dev/null; then
      echo "PX4 SITL exited before headless parameter setup" >&2
      return 1
    fi
    sleep 1
  done
  echo "PX4 SITL did not become ready for headless parameter setup" >&2
  return 1
}

LD_LIBRARY_PATH="${HOME}/.local/lib:${LD_LIBRARY_PATH:-}" \
  "${AGENT_BIN}" udp4 -p "${XRCE_PORT}" &
AGENT_PID=$!
sleep 2
cd "${PX4_ROOT}"
echo "PX4 simulation model: ${SIM_MODEL} (HEADLESS=1)"
HEADLESS=1 make px4_sitl "gz_${SIM_MODEL}" &
PX4_PID=$!
configure_headless_sitl
echo "PX4 SITL is running; press Ctrl-C to stop."
wait "${PX4_PID}"
