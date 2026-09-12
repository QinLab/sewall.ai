#!/bin/bash
# All model experiments and validation run on allocated CPU nodes.
# Submit from the repository root: sbatch --chdir="$PWD" scripts/cpu_job.sh probe
# Paths and commands below are fixed or quoted; never execute model-generated shell.
#SBATCH --job-name=sewall-mvp
#SBATCH --partition=cpu-2
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=2
# Waterfield registers RealMemory=1 as a sentinel. Use its default memory setting.
#SBATCH --time=00:15:00
#SBATCH --output=slurm-sewall-%j.out
#SBATCH --error=slurm-sewall-%j.err

set -euo pipefail
if [[ -z "${SLURM_JOB_ID:-}" || "${SLURM_JOB_PARTITION:-}" != cpu* ]]; then
    echo "This script requires a Slurm CPU allocation" >&2
    exit 2
fi
export OMP_NUM_THREADS="${SLURM_CPUS_PER_TASK:-2}"
export OPENBLAS_NUM_THREADS="${SLURM_CPUS_PER_TASK:-2}"
export MKL_NUM_THREADS="${SLURM_CPUS_PER_TASK:-2}"
export PYTHONUNBUFFERED=1
# MCP clients may supply a minimal environment without exported module functions.
if ! type module >/dev/null 2>&1; then
    set +u
    source /etc/profile.d/modules.sh
    set -u
fi
module load python3
SEWALL_PROJECT_DIR="${SEWALL_PROJECT_DIR:-$PWD}"
SEWALL_ENV="${SEWALL_ENV:-${SEWALL_PROJECT_DIR}/.waterfield-env}"
cd -- "$SEWALL_PROJECT_DIR"
if [[ ! -d "$SEWALL_ENV" ]]; then
    echo "Create the project crun environment with scripts/setup_waterfield_env.sh in an interactive CPU allocation" >&2
    exit 2
fi
SEWALL_TASK="${1:-test}"
shift || true
case "$SEWALL_TASK" in
    probe) exec crun -p "$SEWALL_ENV" python3 scripts/cluster_probe.py "$@" ;;
    test) exec crun -p "$SEWALL_ENV" python3 -m unittest discover -s tests -v "$@" ;;
    models) exec crun -p "$SEWALL_ENV" python3 scripts/probe_models.py "$@" ;;
    agent) exec crun -p "$SEWALL_ENV" python3 -m sewall agent "$@" ;;
    replay) exec crun -p "$SEWALL_ENV" python3 -m sewall agent-replay "$@" ;;
    validate) exec crun -p "$SEWALL_ENV" python3 scripts/validate_live_run.py "$@" ;;
    safety) exec crun -p "$SEWALL_ENV" python3 -m sewall safety-demo "$@" ;;
    safety-verify) exec crun -p "$SEWALL_ENV" python3 -m sewall safety-verify "$@" ;;
    mcp) exec crun -p "$SEWALL_ENV" python3 -m sewall.mcp_server "$@" ;;
    *) echo "Allowed tasks: probe, test, models, agent, replay, validate, safety, safety-verify, mcp" >&2; exit 2 ;;
esac
