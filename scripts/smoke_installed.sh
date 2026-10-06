#!/bin/bash
# Check an installed sewall package from outside the source tree. No network or model calls.
# Usage: bash scripts/smoke_installed.sh [PYTHON]   (sewall must be on PATH for that Python)
set -euo pipefail
PYTHON="${1:-python3}"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
cd "$work"
sewall --version
"$PYTHON" - <<'PY'
from sewall.agent import default_registry
from sewall.skills import LIVE_DIRECTORY
registry = default_registry()
operations = registry.operations()
assert "taxon_inventory" in operations, operations
assert "site-packages" in str(LIVE_DIRECTORY) or "dist-packages" in str(LIVE_DIRECTORY), LIVE_DIRECTORY
print("skills:", LIVE_DIRECTORY, operations)
PY
sewall demo --out demo >/dev/null
sewall replay demo/manifest.json >/dev/null
# The default safety scenario ends in a controlled safe stop, which exits nonzero by design.
sewall safety-demo --out safety >/dev/null || true
sewall safety-verify safety/manifest.json >/dev/null
echo "installed package smoke check passed"
