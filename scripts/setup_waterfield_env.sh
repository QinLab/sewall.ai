#!/bin/bash
# One-time, interactive CPU setup. Do not include installation in recurring jobs.
# From the repository root: srun -p cpu-2 -N 1 -n 1 -c 2 -t 00:10:00 bash scripts/setup_waterfield_env.sh
set -euo pipefail
if [[ -z "${SLURM_JOB_ID:-}" || "${SLURM_JOB_PARTITION:-}" != cpu* ]]; then
    echo "Environment setup requires an allocated Slurm CPU node" >&2
    exit 2
fi
if ! type module >/dev/null 2>&1; then
    set +u
    source /etc/profile.d/modules.sh
    set -u
fi
module load python3
SEWALL_PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
SEWALL_ENV="${SEWALL_ENV:-${SEWALL_PROJECT_DIR}/.waterfield-env}"
if [[ "${1:-}" != "" && "${1:-}" != "--resume" ]]; then
    echo "Only --resume is accepted for an inspected, interrupted setup" >&2
    exit 2
fi
if [[ -e "$SEWALL_ENV" && "${1:-}" != "--resume" ]]; then
    echo "Project environment already exists; inspect it before changing dependencies" >&2
    exit 2
fi
export OMP_NUM_THREADS="${SLURM_CPUS_PER_TASK:-2}"
export OPENBLAS_NUM_THREADS="${SLURM_CPUS_PER_TASK:-2}"
if [[ ! -e "$SEWALL_ENV" ]]; then
    crun -c -p "$SEWALL_ENV"
fi
crun -p "$SEWALL_ENV" python3 -m pip install --disable-pip-version-check --no-cache-dir -r "$SEWALL_PROJECT_DIR/requirements-waterfield.txt"
crun -p "$SEWALL_ENV" python3 -m pip check
