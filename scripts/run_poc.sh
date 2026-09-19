#!/usr/bin/env bash
# DIPTYCH PoC: full-8 core gate + unit tests + matrix check.
# Stranger path (from a clean clone): exit 0 + green×8×3 printout.
# No GPU. No network. No AUROC / invented model scores.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"

if ! command -v python3 >/dev/null 2>&1; then
  echo "error: python3 not found (need Python >= 3.10)" >&2
  exit 1
fi

echo "== DIPTYCH PoC =="
echo "repo: $ROOT"
echo "python: $(python3 -c 'import sys; print(sys.version.split()[0])')"
echo "deps: stdlib-only core (optional: pip install pytest for CI-parity tests)"
echo

python3 -m diptych.run_full8

echo
echo "== coverage matrix (greenx8x3) =="
python3 scripts/print_matrix.py

python3 - <<'PY'
"""Check live matrix against examples/poc expected greenx8x3 snippet."""
from __future__ import annotations

import json
import sys
from pathlib import Path

root = Path(".")
matrix = json.loads((root / "coverage" / "matrix.json").read_text(encoding="utf-8"))
snippet = json.loads(
    (root / "examples" / "poc" / "expected_matrix_snippet.json").read_text(encoding="utf-8")
)

ops = matrix.get("operators", {})
errors: list[str] = []
for op, expect in snippet.get("operators", {}).items():
    cell = ops.get(op)
    if cell is None:
        errors.append(f"{op}: missing from live matrix")
        continue
    for key, want in expect.items():
        got = cell.get(key)
        if got != want:
            errors.append(f"{op}.{key}: expected {want!r}, got {got!r}")

for op, cell in ops.items():
    for col in ("diptych_core", "zeroday", "aomb"):
        if cell.get(col) != "green":
            errors.append(f"{op}.{col}: expected 'green', got {cell.get(col)!r}")
    if cell.get("axis_power") is not True:
        errors.append(f"{op}.axis_power: expected True, got {cell.get('axis_power')!r}")

if errors:
    print("MATRIX CHECK FAIL:")
    for e in errors:
        print(f"  {e}")
    sys.exit(1)

print("MATRIX CHECK OK (greenx8x3; axis_power=true; matches examples/poc snippet)")
print(
    "non-claim: greenx8x3 = harness coverage + axis power "
    "-- not AUROC / accuracy / vuln-finding"
)
PY

echo
echo "== unit tests =="
python3 -m unittest discover -s tests -v
echo
echo "PoC OK -- stranger path complete (clone -> ./scripts/run_poc.sh -> exit 0)"
