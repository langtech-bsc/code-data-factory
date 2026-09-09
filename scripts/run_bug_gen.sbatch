#!/usr/bin/env bash
# Run one filesystem-backed SWE-smith phase on a GPP allocation.
#SBATCH --job-name=swesmith-phase
#SBATCH --account=bsc88
#SBATCH --qos=gp_bscaii
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=112
#SBATCH --time=2-00:00:00
#SBATCH --output=/gpfs/scratch/bsc88/text/models/vision/code-data-factory/logs/slurm/%x_%j.out
#SBATCH --error=/gpfs/scratch/bsc88/text/models/vision/code-data-factory/logs/slurm/%x_%j.err

set -euo pipefail

REPO_ROOT="${SLURM_SUBMIT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
cd "$REPO_ROOT"

export SWESMITH_WORK_ROOT="${SWESMITH_WORK_ROOT:-/gpfs/scratch/bsc88/text/models/vision/code-data-factory}"
export SWESMITH_SIF_ROOT="${SWESMITH_SIF_ROOT:-/gpfs/scratch/bsc88/post-training/singularities/code-data-factory}"
export SWESMITH_TMP_ROOT="${SWESMITH_TMP_ROOT:-${TMPDIR:-/scratch}}"

module load singularity 2>/dev/null || true

phase="${1:?phase is required}"
shift

language="${SWESMITH_LANGUAGE:-javascript}"
repo_spec="${SWESMITH_REPOS:-}"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --language) language="$2"; shift 2 ;;
    --repos) repo_spec="$2"; shift 2 ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
done

args=(--language "$language")
if [[ -n "$repo_spec" ]]; then
  args+=(--repos "$repo_spec")
fi

if [[ "$phase" == "status" ]]; then
  args+=(--phases "${SWESMITH_PHASES:-gen,val,gather,issue}" --status-only)
else
  args+=(--phases "$phase")
fi

args+=(--workers "${MAX_WORKERS:-1}")
args+=(--repo-workers "${SWESMITH_REPO_WORKERS:-4}")
args+=(--issue-gen-workers "${SWESMITH_ISSUE_GEN_WORKERS:-4}")
args+=(--issue-model "${SWESMITH_MODEL:-glm-5.3-flash}")

if [[ -n "${SWESMITH_ISSUE_GEN_CONFIG:-}" ]]; then
  args+=(--issue-gen-config "$SWESMITH_ISSUE_GEN_CONFIG")
fi
if [[ "${SWESMITH_REDO_EXISTING:-0}" == "1" ]]; then
  args+=(--redo-existing)
fi
if [[ "${SWESMITH_OVERRIDE_BRANCH:-0}" == "1" ]]; then
  args+=(--override-branch)
fi
if [[ "${SWESMITH_DEBUG_SUBPROCESS:-0}" == "1" ]]; then
  args+=(--debug-subprocess)
fi
if [[ "${SWESMITH_VERBOSE:-0}" == "1" ]]; then
  args+=(--verbose)
fi

uv run python scripts/bug_gen.py "${args[@]}"
