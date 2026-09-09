#!/usr/bin/env bash
# Build the requested repository execution images on a CPU-only GPP allocation.
# The submit wrapper overrides account, QoS, time, and output paths when needed.
#SBATCH --job-name=swesmith-images
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

language="${SWESMITH_LANGUAGE:-javascript}"
build_workers="${SWESMITH_BUILD_WORKERS:-4}"

repo_spec="${SWESMITH_REPOS:-}"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --language) language="$2"; shift 2 ;;
    --repos) repo_spec="$2"; shift 2 ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
done
args=(--language "$language" --workers "$build_workers" --proceed)

if [[ -n "$repo_spec" ]]; then
  IFS=',' read -r -a repo_args <<< "$repo_spec"
  args+=(--repos "${repo_args[@]}")
fi

uv run python -m swesmith.build_repo.create_images "${args[@]}"
