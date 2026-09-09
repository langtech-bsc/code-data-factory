#!/usr/bin/env bash
# Serve GLM-5.3-Flash through the pinned PTIE Singularity launcher.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PTIE_ROOT="${PTIE_ROOT:-${SCRIPT_DIR}/../posttraining-inference-engine}"
MODEL_PATH="${MODEL_PATH:-/gpfs/projects/bsc88/hf-models/GLM-5.3-Flash}"
PROFILE="${PTIE_PROFILE:-glm-5-3-flash-fp8-h100-mem090-128}"
NODES="${PTIE_NODES:-all}"
INSTANCE="${PTIE_INSTANCE:-glm53-reference}"

if [[ ! -x "${PTIE_ROOT}/launch_server.sh" ]]; then
  echo "PTIE launcher not found at ${PTIE_ROOT}/launch_server.sh" >&2
  exit 2
fi

exec "${PTIE_ROOT}/launch_server.sh" "${MODEL_PATH}" \
  --profile "${PROFILE}" \
  --nodes "${NODES}" \
  --instance "${INSTANCE}" \
  --served-model-name glm-5.3-flash \
  "$@"
