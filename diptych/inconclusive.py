"""RQ5 protocol harness: inconclusive rate on the cassette fixture bank.

Protocol scaffolding on the **handwritten cassette fixture bank only**
(structural counts). No AUROC / no model scores / no empirical RQ5 answer.

1. Walk ``examples/fixtures/cassette/**/probe.json``; grade each via
   ``grade_operator``.
2. Count by expected_verdict, actual_verdict, and inconclusive_reason.
3. Per-operator + aggregate rates::

       inconclusive_rate = n_inconclusive / n_probes

   Stratify by operator and reason needle (horizon/prefix, missing twin,
   crn_stream_id, …).
4. Assert every operator has ≥1 ``inconclusive_*`` fixture that actually
   grades ``inconclusive`` (full-8 coverage).
5. Exit non-zero if any op lacks a working inconclusive fixture **or** any
   fixture in the inconclusive set has expected≠actual.

Aggregate JSON carries explicit ``non_claims``: protocol scaffolding on
fixtures, NOT an empirical RQ5 model-quality answer / NOT AUROC.

Stranger path::

    python -m diptych.inconclusive
    python -m diptych.inconclusive --json
    diptych-inconclusive
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Callable

from diptych import OPERATORS
from diptych.api import grade_operator

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASSETTE = ROOT / "examples" / "fixtures" / "cassette"

EXIT_OK = 0
EXIT_FAIL = 1

# Stable reason-needle strata (substring match on graded inconclusive_reason).
# Order matters: first matching needle wins.
REASON_NEEDLES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("missing_twin", ("missing twin",)),
    ("crn_stream_id", ("crn_stream_id",)),
    (
        "horizon_prefix",
        (
            "shared prefix",
            "length mismatch",
            "channel lengths",
            "horizon",
        ),
    ),
)


def classify_reason_needle(reason: str | None) -> str:
    """Map an inconclusive reason string to a stable stratum needle id."""
    text = (reason or "").lower()
    if not text.strip():
        return "unknown"
    for needle_id, substrings in REASON_NEEDLES:
        for sub in substrings:
            if sub.lower() in text:
                return needle_id
    return "other"


def iter_cassette_probes(cassette: Path | None = None) -> list[Path]:
    """Return sorted ``**/probe.json`` paths under the cassette root."""
    root = Path(cassette) if cassette is not None else DEFAULT_CASSETTE
    if not root.is_dir():
        raise FileNotFoundError(f"cassette root missing: {root}")
    return sorted(root.glob("**/probe.json"))


def _is_inconclusive_fixture_path(path: Path) -> bool:
    """True when a path segment is an ``inconclusive_*`` fixture role dir."""
    return any(part.startswith("inconclusive_") for part in path.parts)


def _verdict_of(result: Any) -> str:
    if hasattr(result, "actual_verdict"):
        return str(result.actual_verdict)
    if isinstance(result, dict):
        return str(result.get("actual_verdict") or "")
    return ""


def _reason_of(result: Any) -> str:
    if hasattr(result, "reason"):
        return str(getattr(result, "reason", "") or "")
    if isinstance(result, dict):
        return str(result.get("reason") or "")
    return ""


def _expected_of(result: Any, doc: dict[str, Any]) -> str:
    if hasattr(result, "expected_verdict"):
        return str(result.expected_verdict)
    if isinstance(result, dict) and result.get("expected_verdict"):
        return str(result.get("expected_verdict") or "")
    return str(doc.get("expected_verdict") or "")


def _matches_expected(result: Any, expected: str, actual: str) -> bool:
    if hasattr(result, "matches_expected"):
        return bool(result.matches_expected)
    if isinstance(result, dict) and "matches_expected" in result:
        return bool(result.get("matches_expected"))
    return expected == actual and expected in {"pass", "fail", "inconclusive"}


def grade_probe_row(
    path: Path,
    *,
    cassette: Path,
    grader: Callable[[dict[str, Any]], Any] | None = None,
) -> dict[str, Any]:
    """Load + grade one cassette probe; return a structural count row."""
    grade = grader or grade_operator
    try:
        rel = str(path.relative_to(cassette))
    except ValueError:
        rel = str(path)

    is_inc_fix = _is_inconclusive_fixture_path(path)

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "path": rel,
            "ok": False,
            "error": f"load_failed:{exc}",
            "operator": None,
            "expected_verdict": None,
            "actual_verdict": None,
            "matches_expected": False,
            "inconclusive_reason": None,
            "reason_needle": None,
            "is_inconclusive_fixture": is_inc_fix,
        }

    if not isinstance(raw, dict):
        return {
            "path": rel,
            "ok": False,
            "error": "probe_not_object",
            "operator": None,
            "expected_verdict": None,
            "actual_verdict": None,
            "matches_expected": False,
            "inconclusive_reason": None,
            "reason_needle": None,
            "is_inconclusive_fixture": is_inc_fix,
        }

    op = str(raw.get("operator") or "").upper() or None
    expected = str(raw.get("expected_verdict") or "")
    is_inc_fix = is_inc_fix or expected == "inconclusive"

    try:
        result = grade(raw)
    except Exception as exc:  # noqa: BLE001 — surface as structural row error
        return {
            "path": rel,
            "ok": False,
            "error": f"grade_failed:{exc}",
            "operator": op,
            "expected_verdict": expected or None,
            "actual_verdict": None,
            "matches_expected": False,
            "inconclusive_reason": None,
            "reason_needle": None,
            "is_inconclusive_fixture": is_inc_fix,
        }

    actual = _verdict_of(result)
    reason = _reason_of(result)
    expected = _expected_of(result, raw) or expected
    matches = _matches_expected(result, expected, actual)
    inc_reason = reason if actual == "inconclusive" else None
    needle = classify_reason_needle(inc_reason) if actual == "inconclusive" else None

    return {
        "path": rel,
        "ok": True,
        "operator": op,
        "expected_verdict": expected,
        "actual_verdict": actual,
        "matches_expected": matches,
        "inconclusive_reason": inc_reason,
        "reason_needle": needle,
        "is_inconclusive_fixture": is_inc_fix,
    }


def evaluate_operator_inconclusive(
    op: str,
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Per-operator structural inconclusive coverage + rate."""
    op_u = op.upper()
    op_rows = [r for r in rows if r.get("operator") == op_u]
    n_probes = len(op_rows)
    n_inconclusive = sum(1 for r in op_rows if r.get("actual_verdict") == "inconclusive")
    rate = (n_inconclusive / n_probes) if n_probes else 0.0

    inc_fixtures = [r for r in op_rows if r.get("is_inconclusive_fixture")]
    working = [
        r
        for r in inc_fixtures
        if r.get("actual_verdict") == "inconclusive" and r.get("matches_expected")
    ]
    mismatches = [
        r
        for r in inc_fixtures
        if not r.get("matches_expected") or r.get("actual_verdict") != "inconclusive"
    ]

    by_needle: Counter[str] = Counter()
    for r in op_rows:
        if r.get("actual_verdict") == "inconclusive":
            by_needle[str(r.get("reason_needle") or "unknown")] += 1

    by_expected: Counter[str] = Counter()
    by_actual: Counter[str] = Counter()
    for r in op_rows:
        by_expected[str(r.get("expected_verdict") or "unknown")] += 1
        by_actual[str(r.get("actual_verdict") or "unknown")] += 1

    return {
        "operator": op_u,
        "n_probes": n_probes,
        "n_inconclusive": n_inconclusive,
        "inconclusive_rate": rate,
        "n_inconclusive_fixtures": len(inc_fixtures),
        "n_working_inconclusive": len(working),
        "has_working_inconclusive": len(working) >= 1,
        "inconclusive_mismatches": [
            {
                "path": r.get("path"),
                "expected_verdict": r.get("expected_verdict"),
                "actual_verdict": r.get("actual_verdict"),
                "error": r.get("error"),
            }
            for r in mismatches
        ],
        "by_expected_verdict": dict(sorted(by_expected.items())),
        "by_actual_verdict": dict(sorted(by_actual.items())),
        "by_reason_needle": dict(sorted(by_needle.items())),
        "working_paths": [r.get("path") for r in working],
    }


def build_inconclusive_report(
    *,
    cassette: Path | None = None,
    grader: Callable[[dict[str, Any]], Any] | None = None,
) -> dict[str, Any]:
    """Full cassette-bank inconclusive-rate report (protocol scaffolding only)."""
    cassette_path = Path(cassette) if cassette is not None else DEFAULT_CASSETTE
    failures: list[str] = []

    non_claims = [
        "NOT AUROC",
        "NOT model scores / ranks",
        "NOT an empirical RQ5 model-quality answer",
        "protocol scaffolding on cassette fixtures only",
    ]
    notes = (
        "RQ5 protocol harness — inconclusive rate on handwritten cassette "
        "fixtures only (structural counts). Walks "
        "examples/fixtures/cassette/**/probe.json; grades each; reports "
        "n_inconclusive/n_probes per operator and aggregate; stratifies by "
        "reason needle (horizon_prefix / missing_twin / crn_stream_id). "
        "Requires every operator to have ≥1 inconclusive_* fixture that "
        "actually grades inconclusive. NOT AUROC / NOT model scores / "
        "NOT an empirical RQ5 model-quality answer."
    )

    try:
        paths = iter_cassette_probes(cassette_path)
    except FileNotFoundError as exc:
        return {
            "ok": False,
            "operator_count": len(OPERATORS),
            "ops_with_working_inconclusive": 0,
            "n_probes": 0,
            "n_inconclusive": 0,
            "inconclusive_rate": 0.0,
            "cassette": str(cassette_path),
            "probes": [],
            "operators": {},
            "by_expected_verdict": {},
            "by_actual_verdict": {},
            "by_reason_needle": {},
            "failures": [f"cassette_missing:{exc}"],
            "notes": notes,
            "non_claims": non_claims,
        }

    rows = [
        grade_probe_row(path, cassette=cassette_path, grader=grader) for path in paths
    ]

    by_expected: Counter[str] = Counter()
    by_actual: Counter[str] = Counter()
    by_needle: Counter[str] = Counter()
    n_inconclusive = 0
    for r in rows:
        by_expected[str(r.get("expected_verdict") or "unknown")] += 1
        by_actual[str(r.get("actual_verdict") or "unknown")] += 1
        if r.get("actual_verdict") == "inconclusive":
            n_inconclusive += 1
            by_needle[str(r.get("reason_needle") or "unknown")] += 1
        if r.get("error"):
            failures.append(f"probe_error:{r.get('path')}")

    n_probes = len(rows)
    aggregate_rate = (n_inconclusive / n_probes) if n_probes else 0.0

    operators: dict[str, Any] = {}
    for op in OPERATORS:
        cell = evaluate_operator_inconclusive(op, rows)
        operators[op] = cell
        if not cell.get("has_working_inconclusive"):
            failures.append(f"{op}:missing_working_inconclusive")
        for mm in cell.get("inconclusive_mismatches") or []:
            failures.append(f"{op}:inconclusive_mismatch:{mm.get('path')}")

    ops_with_working = sum(
        1 for op in OPERATORS if operators[op].get("has_working_inconclusive")
    )
    if ops_with_working < len(OPERATORS):
        failures.append("full8_inconclusive_coverage<8")

    ok = not failures and ops_with_working == len(OPERATORS)
    return {
        "ok": ok,
        "operator_count": len(OPERATORS),
        "ops_with_working_inconclusive": ops_with_working,
        "n_probes": n_probes,
        "n_inconclusive": n_inconclusive,
        "inconclusive_rate": aggregate_rate,
        "cassette": str(cassette_path.resolve()),
        "probes": rows,
        "operators": operators,
        "by_expected_verdict": dict(sorted(by_expected.items())),
        "by_actual_verdict": dict(sorted(by_actual.items())),
        "by_reason_needle": dict(sorted(by_needle.items())),
        "failures": failures,
        "notes": notes,
        "non_claims": non_claims,
    }


def main(argv: list[str] | None = None) -> int:
    """CLI: ``python -m diptych.inconclusive`` / ``diptych-inconclusive``."""
    p = argparse.ArgumentParser(
        prog="diptych-inconclusive",
        description=(
            "RQ5 protocol harness: inconclusive rate on handwritten cassette "
            "fixtures (structural counts only — not AUROC / model score / "
            "empirical RQ5 answer). Exit non-zero if any op lacks a working "
            "inconclusive fixture or grade/expected mismatch on the "
            "inconclusive set."
        ),
    )
    p.add_argument(
        "--cassette",
        type=Path,
        default=DEFAULT_CASSETTE,
        help=(
            "Cassette root with {OP}/**/probe.json "
            f"(default: {DEFAULT_CASSETTE})"
        ),
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON report on stdout",
    )
    args = p.parse_args(list(argv) if argv is not None else None)

    report = build_inconclusive_report(cassette=args.cassette)
    if args.json:
        # Drop bulky per-probe rows from CLI JSON unless useful — keep them;
        # ablation/separation keep full structure. Keep probes for debugging.
        sys.stdout.write(json.dumps(report, indent=2) + "\n")
    else:
        print("== DIPTYCH inconclusive (RQ5 protocol harness) ==")
        print(f"cassette={report['cassette']}")
        print(
            f"n_probes={report['n_probes']} "
            f"n_inconclusive={report['n_inconclusive']} "
            f"inconclusive_rate={report['inconclusive_rate']}"
        )
        print(
            f"ops_with_working_inconclusive="
            f"{report['ops_with_working_inconclusive']}/"
            f"{report['operator_count']}"
        )
        print(
            "(structural counts on cassette fixtures — NOT AUROC / "
            "model score / empirical RQ5 answer)"
        )
        print(f"by_reason_needle={report.get('by_reason_needle')}")
        for op, cell in (report.get("operators") or {}).items():
            status = "OK  " if cell.get("has_working_inconclusive") else "FAIL"
            print(
                f"  {status} {op:12} "
                f"n_probes={cell.get('n_probes')} "
                f"n_inconclusive={cell.get('n_inconclusive')} "
                f"rate={cell.get('inconclusive_rate')} "
                f"needles={cell.get('by_reason_needle')}"
            )
        if report["ok"]:
            print("INCONCLUSIVE OK")
        else:
            print(f"INCONCLUSIVE FAIL ({', '.join(report['failures'])})")
    return EXIT_OK if report["ok"] else EXIT_FAIL


if __name__ == "__main__":
    raise SystemExit(main())
