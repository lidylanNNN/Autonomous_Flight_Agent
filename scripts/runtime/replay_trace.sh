#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VENV_PYTHON="${PROJECT_ROOT}/.venv/bin/python"

[[ -x "${VENV_PYTHON}" ]] || {
  echo "Project virtual environment not found: ${VENV_PYTHON}" >&2
  exit 1
}

export PYTHONPATH="${PROJECT_ROOT}/src:${PYTHONPATH:-}"
exec "${VENV_PYTHON}" -m flight_agent.runtime.trace_cli "$@"
