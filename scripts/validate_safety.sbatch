#!/bin/bash
# One CPU is sufficient for the offline tests and the synthetic safe-stop demonstrations.
# Submit from the repository root: sbatch --chdir="$PWD" scripts/validate_safety.sbatch
#SBATCH --job-name=sewall-safe-stop
#SBATCH --partition=cpu-2
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --time=00:15:00
#SBATCH --output=slurm-sewall-safe-stop-%j.out
#SBATCH --error=slurm-sewall-safe-stop-%j.err
set -euo pipefail
if [[ -z "${SLURM_JOB_ID:-}" || "${SLURM_JOB_PARTITION:-}" != cpu* ]]; then
    echo "Safe-stop validation requires an allocated Slurm CPU node" >&2
    exit 2
fi
if ! type module >/dev/null 2>&1; then
    set +u
    source /etc/profile.d/modules.sh
    set -u
fi
module load python3
export OMP_NUM_THREADS="${SLURM_CPUS_PER_TASK:-1}"
export OPENBLAS_NUM_THREADS="${SLURM_CPUS_PER_TASK:-1}"
export MKL_NUM_THREADS="${SLURM_CPUS_PER_TASK:-1}"
export PYTHONUNBUFFERED=1
# Point SEWALL_NODE_BIN at a verified Node.js executable; the report syntax test needs it.
if [[ ! -x "${SEWALL_NODE_BIN:-}" ]]; then
    echo "Set SEWALL_NODE_BIN to a verified Node.js executable for the report syntax test" >&2
    exit 2
fi
SEWALL_SAFETY_ENV="${SEWALL_ENV:-$PWD/.waterfield-env}"
crun -p "$SEWALL_SAFETY_ENV" python3 scripts/validate_safety.py
