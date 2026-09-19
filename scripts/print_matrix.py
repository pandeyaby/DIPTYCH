#!/usr/bin/env python3
"""Print coverage/matrix.json as a green×8×3 table (stranger / make matrix)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MATRIX = ROOT / "coverage" / "matrix.json"


def main() -> int:
    if not MATRIX.is_file():
        print(f"missing {MATRIX}; run ./scripts/run_poc.sh or make full8 first", file=sys.stderr)
        return 1
    matrix = json.loads(MATRIX.read_text(encoding="utf-8"))
    ops = matrix.get("operators", {})
    print(f"schema={matrix.get('diptych_schema')} source_row={matrix.get('source_row')}")
    print(f"{'operator':12} {'core':8} {'zeroday':8} {'aomb':8} axis_power")
    for op, cell in ops.items():
        print(
            f"{op:12} {str(cell.get('diptych_core')):8} "
            f"{str(cell.get('zeroday')):8} {str(cell.get('aomb')):8} "
            f"{cell.get('axis_power')}"
        )
    print(
        "note: greenx8x3 = coverage + axis power "
        "(not AUROC / accuracy / vuln-finding)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
