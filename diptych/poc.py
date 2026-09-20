#!/usr/bin/env python3
"""Stranger-facing PoC runner: clone → one command → structured JSON (optional SARIF).

Emits machine-readable results for all 8 operators × conforming/violating plus
existing gate_axis_mutate axis-power evidence. No AUROC / invented model scores.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from diptych import OPERATORS, SCHEMA
from diptych.api import grade_operator, parse_probe
from diptych.gates import Report, run_gates, write_matrix

POC_SCHEMA = "1.0"
DEFAULT_JSON_OUT = ROOT / "reports" / "paired-probes" / "poc_report.json"
DEFAULT_SARIF_OUT = ROOT / "reports" / "paired-probes" / "poc_report.sarif"
DEFAULT_MATRIX = ROOT / "coverage" / "matrix.json"

# Top-level keys a stranger / CI parser can rely on.
POC_REQUIRED_KEYS = (
    "poc_schema",
    "diptych_schema",
    "ok",
    "operators",
    "matrix",
    "axis_power",
    "failures",
    "operator_count",
)

OPERATOR_REQUIRED_KEYS = (
    "operator",
    "conforming",
    "violating",
    "gate_axis_mutate",
    "diptych_core",
    "axis_power",
)

ROLE_REQUIRED_KEYS = (
    "control_role",
    "expected_verdict",
    "actual_verdict",
    "matches_expected",
    "reason",
    "probe_id",
)


def _role_from_results(results: list[dict[str, Any]], op: str, role: str) -> dict[str, Any]:
    for r in results:
        if r.get("operator") == op and r.get("control_role") == role:
            return {
                "control_role": role,
                "probe_id": r.get("probe_id"),
                "expected_verdict": r.get("expected_verdict"),
                "actual_verdict": r.get("actual_verdict"),
                "matches_expected": bool(r.get("matches_expected")),
                "reason": r.get("reason"),
                "evidence": r.get("evidence") or {},
            }
    expected = "pass" if role == "conforming" else "fail"
    return {
        "control_role": role,
        "probe_id": None,
        "expected_verdict": expected,
        "actual_verdict": None,
        "matches_expected": False,
        "reason": "missing grade result",
        "evidence": {},
    }


def build_poc_report(report: Report | None = None) -> dict[str, Any]:
    """Build stranger-facing PoC JSON from a full-8 gate Report."""
    report = report or run_gates()
    operators: dict[str, Any] = {}
    for op in OPERATORS:
        cell = (report.matrix.get("operators") or {}).get(op) or {}
        power = report.axis_power.get(op) or {}
        # Typed API path: parse fixture envelopes (same contract strangers import).
        conf_path = ROOT / "diptych-probes" / op / "conforming" / "probe.json"
        viol_path = ROOT / "diptych-probes" / op / "violating" / "probe.json"
        conf_env = parse_probe(conf_path) if conf_path.is_file() else None
        viol_env = parse_probe(viol_path) if viol_path.is_file() else None
        conf_grade = grade_operator(conf_env) if conf_env is not None else None
        viol_grade = grade_operator(viol_env) if viol_env is not None else None
        # Prefer gate report results; typed grades must agree when both present.
        conforming = _role_from_results(report.results, op, "conforming")
        violating = _role_from_results(report.results, op, "violating")
        if conf_grade is not None and conforming.get("actual_verdict") is not None:
            assert conf_grade.actual_verdict == conforming["actual_verdict"], op
        if viol_grade is not None and violating.get("actual_verdict") is not None:
            assert viol_grade.actual_verdict == violating["actual_verdict"], op
        operators[op] = {
            "operator": op,
            "coupling": conf_env.coupling if conf_env is not None else None,
            "conforming": conforming,
            "violating": violating,
            "gate_axis_mutate": {
                "mutation": power.get("mutation", ""),
                "expected_axis": power.get("expected_axis"),
                "channel": power.get("channel"),
                "baseline_verdict": power.get("baseline_verdict"),
                "mutated_verdict": power.get("mutated_verdict"),
                "axis_changed": bool(power.get("axis_changed")),
                "power_ok": bool(power.get("power_ok")),
                "baseline_reason": power.get("baseline_reason"),
                "mutated_reason": power.get("mutated_reason"),
                "semantic_witness": power.get("semantic_witness"),
                "probe_tree_branch": power.get("probe_tree_branch"),
            },
            "diptych_core": cell.get("diptych_core"),
            "axis_power": bool(cell.get("axis_power")),
            "zeroday": cell.get("zeroday"),
            "aomb": cell.get("aomb"),
        }

    return {
        "poc_schema": POC_SCHEMA,
        "diptych_schema": SCHEMA,
        "ok": bool(report.ok),
        "operator_count": len(OPERATORS),
        "operators": operators,
        "matrix": report.matrix,
        "axis_power": report.axis_power,
        "failures": [f.__dict__ for f in report.failures],
        "api": {
            "import": "from diptych import parse_probe, grade_operator, run_gate_axis_mutate",
            "schema": "diptych_schema 0.2",
        },
        "notes": (
            "PoC JSON = twin conforming/violating grades + gate_axis_mutate axis power; "
            "typed API: parse_probe / grade_operator / run_gate_axis_mutate; "
            "not AUROC / accuracy / vuln-finding"
        ),
    }


def report_to_sarif(poc: dict[str, Any]) -> dict[str, Any]:
    """Map PoC verdicts into SARIF 2.1.0 (optional export; no invented scores)."""
    results: list[dict[str, Any]] = []
    for op, cell in (poc.get("operators") or {}).items():
        for role in ("conforming", "violating"):
            role_cell = cell.get(role) or {}
            matches = bool(role_cell.get("matches_expected"))
            actual = role_cell.get("actual_verdict")
            expected = role_cell.get("expected_verdict")
            level = "none" if matches else "error"
            results.append(
                {
                    "ruleId": f"diptych.{op}.{role}",
                    "level": level,
                    "message": {
                        "text": (
                            f"{op}/{role}: expected={expected} actual={actual} "
                            f"matches_expected={matches} "
                            f"({role_cell.get('reason')})"
                        )
                    },
                    "properties": {
                        "operator": op,
                        "control_role": role,
                        "expected_verdict": expected,
                        "actual_verdict": actual,
                        "matches_expected": matches,
                        "probe_id": role_cell.get("probe_id"),
                    },
                }
            )
        power = cell.get("gate_axis_mutate") or {}
        power_ok = bool(power.get("power_ok"))
        results.append(
            {
                "ruleId": f"diptych.{op}.gate_axis_mutate",
                "level": "none" if power_ok else "error",
                "message": {
                    "text": (
                        f"{op}/gate_axis_mutate: "
                        f"{power.get('baseline_verdict')}→{power.get('mutated_verdict')} "
                        f"power_ok={power_ok} axis_changed={power.get('axis_changed')} "
                        f"({power.get('mutation')})"
                    )
                },
                "properties": {
                    "operator": op,
                    "gate": "gate_axis_mutate",
                    "power_ok": power_ok,
                    "baseline_verdict": power.get("baseline_verdict"),
                    "mutated_verdict": power.get("mutated_verdict"),
                    "axis_changed": bool(power.get("axis_changed")),
                    "expected_axis": power.get("expected_axis"),
                    "channel": power.get("channel"),
                },
            }
        )

    return {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "diptych.poc",
                        "informationUri": "https://github.com/pandeyaby/DIPTYCH",
                        "version": POC_SCHEMA,
                        "rules": [
                            {
                                "id": "diptych.poc",
                                "shortDescription": {
                                    "text": "DIPTYCH full-8 PoC twin grades + gate_axis_mutate"
                                },
                                "fullDescription": {
                                    "text": (
                                        "Harness coverage: conforming pass, violating fail, "
                                        "and axis-power mutate flip. Not AUROC or model quality."
                                    )
                                },
                            }
                        ],
                    }
                },
                "results": results,
                "properties": {
                    "poc_schema": poc.get("poc_schema"),
                    "diptych_schema": poc.get("diptych_schema"),
                    "ok": poc.get("ok"),
                    "operator_count": poc.get("operator_count"),
                },
            }
        ],
    }


def _print_human(poc: dict[str, Any]) -> None:
    print(f"poc_schema={poc['poc_schema']} diptych_schema={poc['diptych_schema']} ok={poc['ok']}")
    print(f"{'operator':12} {'conf':6} {'viol':6} mutate          axis_power")
    for op in OPERATORS:
        cell = poc["operators"][op]
        conf = cell["conforming"].get("actual_verdict")
        viol = cell["violating"].get("actual_verdict")
        power = cell["gate_axis_mutate"]
        mutate = f"{power.get('baseline_verdict')}→{power.get('mutated_verdict')}"
        print(
            f"{op:12} {str(conf):6} {str(viol):6} {mutate:14} {cell.get('axis_power')}"
        )
    if poc.get("failures"):
        print("FAILURES:")
        for f in poc["failures"]:
            print(f"  [{f.get('gate')}] {f.get('detail')}")
    print("POC", "PASS" if poc["ok"] else "FAIL")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="python -m diptych.poc",
        description=(
            "Run full-8 DIPTYCH PoC and emit structured JSON "
            "(optional SARIF). Stdlib-only; no network."
        ),
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Write/print machine-readable PoC JSON (required deliverable path)",
    )
    p.add_argument(
        "--sarif",
        action="store_true",
        help="Also emit SARIF 2.1.0 alongside JSON",
    )
    p.add_argument(
        "--out",
        type=Path,
        default=None,
        help=f"JSON output path (default: {DEFAULT_JSON_OUT} when --json)",
    )
    p.add_argument(
        "--sarif-out",
        type=Path,
        default=None,
        help=f"SARIF output path (default: {DEFAULT_SARIF_OUT} when --sarif)",
    )
    p.add_argument(
        "--matrix",
        type=Path,
        default=DEFAULT_MATRIX,
        help="Write coverage matrix JSON here",
    )
    p.add_argument(
        "--stdout-only",
        action="store_true",
        help="With --json: print JSON to stdout only (no file write)",
    )
    p.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress human table (JSON/SARIF still emitted when requested)",
    )
    args = p.parse_args(argv)

    # Default stranger machine path: --json implied when only writing structured out.
    emit_json = args.json or args.stdout_only or args.out is not None or args.sarif

    report = run_gates()
    write_matrix(report.matrix, args.matrix)
    poc = build_poc_report(report)

    if not args.quiet and not args.stdout_only:
        _print_human(poc)

    if emit_json:
        text = json.dumps(poc, indent=2) + "\n"
        if args.stdout_only:
            sys.stdout.write(text)
        else:
            out = args.out or DEFAULT_JSON_OUT
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(text, encoding="utf-8")
            if not args.quiet:
                print(f"json -> {out}")

    if args.sarif:
        sarif = report_to_sarif(poc)
        sarif_text = json.dumps(sarif, indent=2) + "\n"
        sarif_out = args.sarif_out or DEFAULT_SARIF_OUT
        if args.stdout_only and not emit_json:
            sys.stdout.write(sarif_text)
        else:
            sarif_out.parent.mkdir(parents=True, exist_ok=True)
            sarif_out.write_text(sarif_text, encoding="utf-8")
            if not args.quiet and not args.stdout_only:
                print(f"sarif -> {sarif_out}")

    if not args.quiet and not args.stdout_only:
        print(f"matrix -> {args.matrix}")

    return 0 if poc["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
