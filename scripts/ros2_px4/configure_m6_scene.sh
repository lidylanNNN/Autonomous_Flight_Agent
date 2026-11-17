#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: $0 target|empty (requires running default world with M6 resource path)"
}
if [[ $# -eq 1 && ( "$1" == "--help" || "$1" == "-h" ) ]]; then
  usage
  exit 0
elif [[ $# -ne 1 || ( "$1" != "target" && "$1" != "empty" ) ]]; then
  usage >&2
  exit 2
fi
SCENE="$1"
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SCENE_DIR="${PROJECT_ROOT}/configs/m6"
(cd "${SCENE_DIR}" && sha256sum --check --status resources.sha256)

# Query first: an unavailable world must fail rather than look like an empty one.
scene_info="$(gz service -s /world/default/scene/info \
  --reqtype gz.msgs.Empty --reptype gz.msgs.Scene --timeout 5000 --req '')"
[[ "${scene_info}" == *'name: "ground_plane"'* ]] || {
  echo "Cannot query Gazebo default world" >&2
  exit 1
}
if grep -qE '^[[:space:]]*name: "m6_person"$' <<<"${scene_info}"; then
  reply="$(gz service -s /world/default/remove \
    --reqtype gz.msgs.Entity --reptype gz.msgs.Boolean --timeout 5000 \
    --req 'name: "m6_person" type: MODEL')"
  grep -qx 'data: true' <<<"${reply}" || { echo "Failed to remove m6_person" >&2; exit 1; }
fi
if [[ "${SCENE}" == "target" ]]; then
  reply="$(gz service -s /world/default/create \
    --reqtype gz.msgs.EntityFactory --reptype gz.msgs.Boolean --timeout 5000 \
    --req "sdf_filename: \"${SCENE_DIR}/person.sdf\" allow_renaming: false")"
  grep -qx 'data: true' <<<"${reply}" || { echo "Failed to create m6_person" >&2; exit 1; }
fi
echo "M6 scene request accepted: ${SCENE}; verify actual world state before use."
