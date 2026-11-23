#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: $0 [--model x500|x500_mono_cam] [--world default|m7-warehouse]"
}

SIM_MODEL="x500"
SIM_WORLD="default"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --help|-h) usage; exit 0 ;;
    --model|--world)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      if [[ "$1" == "--model" ]]; then SIM_MODEL="$2"; else SIM_WORLD="$2"; fi
      shift 2 ;;
    *) usage >&2; exit 2 ;;
  esac
done
case "${SIM_MODEL}" in x500|x500_mono_cam) ;; *) usage >&2; exit 2 ;; esac
case "${SIM_WORLD}" in default|m7-warehouse) ;; *) usage >&2; exit 2 ;; esac
if [[ "${SIM_WORLD}" == "m7-warehouse" && "${SIM_MODEL}" != "x500_mono_cam" ]]; then
  echo "m7-warehouse requires --model x500_mono_cam" >&2
  exit 2
fi
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

PX4_ROOT="${PX4_ROOT:-${HOME}/PX4-Autopilot}"
AGENT_BIN="${XRCE_AGENT_BIN:-${HOME}/.local/bin/MicroXRCEAgent}"
XRCE_PORT="${XRCE_PORT:-8888}"
PX4_PARAM_BIN="${PX4_ROOT}/build/px4_sitl_default/bin/px4-param"
PX4_ROOTFS="${PX4_ROOT}/build/px4_sitl_default/rootfs"

[[ -x "${AGENT_BIN}" ]] || { echo "MicroXRCEAgent not found: ${AGENT_BIN}" >&2; exit 1; }
[[ -d "${PX4_ROOT}" ]] || { echo "PX4 source not found: ${PX4_ROOT}" >&2; exit 1; }

cleanup() {
  stop_group "${PX4_PID:-}"
  stop_group "${AGENT_PID:-}"
  stop_group "${WORLD_PID:-}"
}

stop_group() {
  local pid="$1" signal attempt
  [[ -n "${pid}" ]] || return 0
  for signal in INT TERM KILL; do
    kill -"${signal}" -- "-${pid}" 2>/dev/null || break
    for ((attempt=0; attempt<10; attempt++)); do
      kill -0 -- "-${pid}" 2>/dev/null || break
      sleep 0.1
    done
  done
  wait "${pid}" 2>/dev/null || true
}

start_warehouse() {
  local prepared scene entity ready deadline existing_topics
  prepared="$(/usr/bin/python3 "${PROJECT_ROOT}/scripts/ros2_px4/prepare_m7_warehouse.py")"
  if pgrep -x px4 >/dev/null || pgrep -x MicroXRCEAgent >/dev/null; then
    echo "Existing PX4/XRCE instance; refusing to start warehouse" >&2
    return 1
  fi
  existing_topics="$(timeout 5 gz topic -l)"
  if grep -q '^/world/' <<<"${existing_topics}"; then
    echo "Existing Gazebo world in current partition; refusing to attach" >&2
    return 1
  fi
  mapfile -t warehouse_args < <(/usr/bin/python3 -c '
import json, sys
p = json.load(sys.stdin)
print(p["world_path"]); print(p["resource_path"]); print(p["spawn_pose"])
print("\n".join(p["required_entities"]))' <<<"${prepared}")
  export GZ_SIM_RESOURCE_PATH="${warehouse_args[1]}${GZ_SIM_RESOURCE_PATH:+:${GZ_SIM_RESOURCE_PATH}}"
  export GZ_SIM_SYSTEM_PLUGIN_PATH="${PX4_ROOT}/build/px4_sitl_default/src/modules/simulation/gz_plugins${GZ_SIM_SYSTEM_PLUGIN_PATH:+:${GZ_SIM_SYSTEM_PLUGIN_PATH}}"
  export GZ_SIM_SERVER_CONFIG_PATH="${PX4_ROOT}/src/modules/simulation/gz_bridge/server.config"
  export PX4_GZ_MODELS="${PX4_ROOT}/Tools/simulation/gz/models"
  export PX4_GZ_MODEL_POSE="${warehouse_args[2]}"
  GZ_FUEL_CACHE_PATH="$(dirname "${warehouse_args[0]}")/fuel_cache"
  export GZ_FUEL_CACHE_PATH
  mkdir -p "${GZ_FUEL_CACHE_PATH}"
  echo "Warehouse preparation: ${prepared}"
  setsid gz sim -r -s "${warehouse_args[0]}" &
  WORLD_PID=$!
  deadline=$((SECONDS + 35))
  while (( SECONDS < deadline )); do
    kill -0 "${WORLD_PID}" 2>/dev/null || { echo "Warehouse exited before ready" >&2; return 1; }
    scene="$(timeout 6 gz service -s /world/default/scene/info --reqtype gz.msgs.Empty \
      --reptype gz.msgs.Scene --timeout 5000 --req '' 2>/dev/null)" || continue
    ready=true
    for entity in "${warehouse_args[@]:3}"; do
      grep -Fqx "  name: \"${entity}\"" <<<"${scene}" || { ready=false; break; }
    done
    if [[ "${ready}" == true ]] && timeout 5 gz topic -l | grep -qx /world/default/clock; then
      echo "M7 warehouse world ready (default; required entities verified)"
      return 0
    fi
    sleep 0.2
  done
  echo "Warehouse readiness timeout" >&2
  return 1
}

trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
if [[ "${SIM_WORLD}" == "m7-warehouse" ]]; then
  start_warehouse
fi

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
  setsid "${AGENT_BIN}" udp4 -p "${XRCE_PORT}" &
AGENT_PID=$!
sleep 2
kill -0 "${AGENT_PID}"
cd "${PX4_ROOT}"
echo "PX4 simulation model: ${SIM_MODEL} (HEADLESS=1)"
make px4_sitl
# Bash 后台命令可能从 /dev/null 读取；冻结 PX4 的 pxh 会在 EOF 时忙循环。
# 复用 gz_<model> 目标的环境，以 -d 禁用本地 pxh，保留飞控日志和信号退出。
PX4_SIM_MODEL="gz_${SIM_MODEL}" GZ_IP=127.0.0.1 HEADLESS=1 \
  setsid "${PX4_ROOT}/build/px4_sitl_default/bin/px4" -d &
PX4_PID=$!
configure_headless_sitl
echo "PX4 SITL process started; verify ground state and camera before use."
if [[ "${SIM_WORLD}" == "m7-warehouse" ]]; then
  wait -n "${PX4_PID}" "${AGENT_PID}" "${WORLD_PID}"
  echo "Warehouse subprocess exited" >&2
  exit 1
fi
wait "${PX4_PID}"
