#!/usr/bin/env bash
# Submit image build and dependent SWE-smith phases to Slurm.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

ACCOUNT="${SWESMITH_SLURM_ACCOUNT:-bsc88}"
QOS="${SWESMITH_SLURM_QOS:-gp_bscaii}"
TIME_LIMIT="${SWESMITH_SLURM_TIME:-2-00:00:00}"
LANGUAGE="javascript"
REPOS=""
PHASES="gen,val,gather,issue"
WORK_ROOT="${SWESMITH_WORK_ROOT:-/gpfs/scratch/bsc88/text/models/vision/code-data-factory}"
SIF_ROOT="${SWESMITH_SIF_ROOT:-/gpfs/scratch/bsc88/post-training/singularities/code-data-factory}"
REPO_WORKERS="${SWESMITH_REPO_WORKERS:-4}"
WORKERS="${MAX_WORKERS:-1}"
ISSUE_GEN_WORKERS="${SWESMITH_ISSUE_GEN_WORKERS:-4}"
API_BASE="${SWESMITH_API_BASE:-http://127.0.0.1:8000/v1}"
MODEL="${SWESMITH_MODEL:-glm-5.3-flash}"
DRY_RUN=0

usage() {
  sed -n '1,35p' "$0"
  cat <<'EOF'

Options:
  --language NAME              Profile language (default: javascript)
  --repos OWNER/REPO,...       Restrict the run to repositories
  --phases LIST                 Comma-separated phases: gen,val,gather,issue,reconstruct
                                (default: gen,val,gather,issue; reconstruct is opt-in)
  --repo-workers N              Concurrent generation repositories (default: 4)
  --workers N                  Validation workers per repository (default: 1)
  --issue-gen-workers N         Issue-generation workers (default: 4)
  --work-root PATH              Persistent GPFS run root
  --sif-root PATH               Shared SIF cache root
  --account NAME                Slurm account (default: bsc88)
  --qos NAME                    Slurm QoS (default: gp_bscaii)
  --time LIMIT                  Slurm time limit (default: 2-00:00:00)
  --api-base URL                PTIE endpoint
  --model NAME                  PTIE model name
  --dry-run                     Print submissions without calling sbatch
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --language) LANGUAGE="$2"; shift 2 ;;
    --repos) REPOS="$2"; shift 2 ;;
    --phases) PHASES="$2"; shift 2 ;;
    --repo-workers) REPO_WORKERS="$2"; shift 2 ;;
    --workers) WORKERS="$2"; shift 2 ;;
    --issue-gen-workers) ISSUE_GEN_WORKERS="$2"; shift 2 ;;
    --work-root) WORK_ROOT="$2"; shift 2 ;;
    --sif-root) SIF_ROOT="$2"; shift 2 ;;
    --account) ACCOUNT="$2"; shift 2 ;;
    --qos) QOS="$2"; shift 2 ;;
    --time) TIME_LIMIT="$2"; shift 2 ;;
    --api-base) API_BASE="$2"; shift 2 ;;
    --model) MODEL="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ "$DRY_RUN" == "0" && ! -x "$(command -v sbatch || true)" ]]; then
  echo "sbatch is unavailable; run this command inside an MN5 Slurm environment or use --dry-run" >&2
  exit 2
fi

mkdir -p "$WORK_ROOT/logs/slurm"
export_vars="ALL,SWESMITH_WORK_ROOT=$WORK_ROOT,SWESMITH_SIF_ROOT=$SIF_ROOT,SWESMITH_LANGUAGE=$LANGUAGE,SWESMITH_REPO_WORKERS=$REPO_WORKERS,MAX_WORKERS=$WORKERS,SWESMITH_ISSUE_GEN_WORKERS=$ISSUE_GEN_WORKERS,SWESMITH_API_BASE=$API_BASE,SWESMITH_MODEL=$MODEL,SWESMITH_PHASES=$PHASES"

submit() {
  local name="$1" dependency="$2" script="$3"
  shift 3
  local command=(sbatch --parsable --chdir "$REPO_ROOT" --job-name "$name" --account "$ACCOUNT" --qos "$QOS" --time "$TIME_LIMIT" --nodes 1 --ntasks 1 --cpus-per-task 112 --export "$export_vars" --output "$WORK_ROOT/logs/slurm/%x_%j.out" --error "$WORK_ROOT/logs/slurm/%x_%j.err")
  if [[ -n "$dependency" ]]; then
    command+=(--dependency "$dependency")
  fi
  command+=("$script" "$@")
  if [[ "$DRY_RUN" == "1" ]]; then
    printf 'DRY RUN:' >&2
    printf ' %q' "${command[@]}" >&2
    printf '\n' >&2
    echo "dry-run-${name}"
  else
    "${command[@]}" | cut -d';' -f1
  fi
}

build_args=(--language "$LANGUAGE")
[[ -n "$REPOS" ]] && build_args+=(--repos "$REPOS")
build_job="$(submit swesmith-images "" "$SCRIPT_DIR/build_images.sbatch" "${build_args[@]}")"
previous="$build_job"
IFS=',' read -r -a phase_list <<< "$PHASES"
for phase in "${phase_list[@]}"; do
  [[ -n "$phase" ]] || continue
  case "$phase" in
    gen|val|gather|issue|reconstruct) ;;
    *) echo "Invalid phase: $phase" >&2; exit 2 ;;
  esac
  dependency="afterany:$previous"
  [[ "$previous" == "$build_job" ]] && dependency="afterok:$previous"
  phase_args=("$phase" --language "$LANGUAGE")
  [[ -n "$REPOS" ]] && phase_args+=(--repos "$REPOS")
  previous="$(submit "swesmith-$phase" "$dependency" "$SCRIPT_DIR/run_bug_gen.sbatch" "${phase_args[@]}")"
done

summary_args=(status --language "$LANGUAGE")
[[ -n "$REPOS" ]] && summary_args+=(--repos "$REPOS")
summary_job="$(submit swesmith-summary "afterany:$previous" "$SCRIPT_DIR/run_bug_gen.sbatch" "${summary_args[@]}")"
cat <<EOF
Submitted SWE-smith Slurm chain.
  image build: $build_job
  final summary: $summary_job
  work root: $WORK_ROOT
  SIF root: $SIF_ROOT
EOF
