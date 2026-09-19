#!/usr/bin/env bash
# DIPTYCH PoC: full-8 core gate + unit tests. Exit 0 on green.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"

echo "== DIPTYCH PoC =="
echo "repo: $ROOT"
python3 -m diptych.run_full8
python3 -m unittest discover -s tests -v
echo "PoC OK"
