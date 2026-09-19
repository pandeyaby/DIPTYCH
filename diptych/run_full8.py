#!/usr/bin/env python3
"""Run full-8 DIPTYCH probe gates for AOMB and emit coverage matrix."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from diptych.gates import run_gates, write_matrix


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--out",
        type=Path,
        default=ROOT / "reports" / "paired-probes" / "full8_gate_report.json",
    )
    p.add_argument(
        "--matrix",
        type=Path,
        default=ROOT / "coverage" / "matrix.json",
    )
    args = p.parse_args(argv)

    report = run_gates()
    write_matrix(report.matrix, args.matrix)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report.to_dict(), indent=2) + "\n", encoding="utf-8")

    print(f"diptych_schema={report.matrix.get('diptych_schema')} source={report.matrix.get('source_row')}")
    print("operator matrix (diptych_core column; green only after gate_axis_mutate):")
    for op, cell in report.matrix.get("operators", {}).items():
        power = report.axis_power.get(op, {})
        print(
            f"  {op:12} diptych_core={cell.get('diptych_core')} "
            f"axis_power={cell.get('axis_power')} "
            f"mutate={power.get('baseline_verdict')}→{power.get('mutated_verdict')}"
        )
    if report.failures:
        print("FAILURES:")
        for f in report.failures:
            print(f"  [{f.gate}] {f.detail}")
    print(f"report -> {args.out}")
    print(f"matrix -> {args.matrix}")
    print("GATE", "PASS" if report.ok else "FAIL")
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
