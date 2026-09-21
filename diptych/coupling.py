"""Coupling-discipline gate: open_loop vs crn_closed_loop (harness-only).

Verifies every ``ops/*/spec.yaml`` and every cassette ``probe.json`` coupling
field against the canonical operator→coupling map. Exit non-zero on drift.
Machine-readable JSON via ``--json``. No invented scores / AUROC.

Canonical map (from ops/*/spec.yaml + docs/adapters/OPERATOR_TABLE.md)::

    open_loop:       FREEZEDRY, RESEED, SCHEMAX, SIGNFLIP, SATEXTEND, HISTSWAP
    crn_closed_loop: TRAJSWAP, VARSCALE

Stranger path::

    python -m diptych.coupling --check
    python -m diptych.coupling --check --json
    diptych-coupling --check
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from diptych import COUPLINGS, CRN_REQUIRED, OPERATORS

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OPS = ROOT / "ops"
DEFAULT_CASSETTE = ROOT / "examples" / "fixtures" / "cassette"

EXIT_OK = 0
EXIT_FAIL = 1

# Canonical operator → coupling (must stay aligned with ops/*/spec.yaml).
CANONICAL_COUPLING: dict[str, str] = {
    "FREEZEDRY": "open_loop",
    "RESEED": "open_loop",
    "SCHEMAX": "open_loop",
    "SIGNFLIP": "open_loop",
    "SATEXTEND": "open_loop",
    "HISTSWAP": "open_loop",
    "TRAJSWAP": "crn_closed_loop",
    "VARSCALE": "crn_closed_loop",
}

_COUPLING_LINE_RE = re.compile(r"^coupling:\s*(\S+)\s*$", re.MULTILINE)
_OPERATOR_LINE_RE = re.compile(r"^operator:\s*(\S+)\s*$", re.MULTILINE)


def expected_coupling(op: str) -> str:
    """Return canonical coupling for an operator name."""
    op_u = op.upper()
    if op_u not in CANONICAL_COUPLING:
        raise KeyError(f"unknown operator {op!r}")
    return CANONICAL_COUPLING[op_u]


def _parse_spec_coupling(text: str) -> str | None:
    m = _COUPLING_LINE_RE.search(text)
    return m.group(1) if m else None


def _parse_spec_operator(text: str) -> str | None:
    m = _OPERATOR_LINE_RE.search(text)
    return m.group(1) if m else None


def _check_entry(
    *,
    path: Path,
    kind: str,
    operator: str | None,
    actual: str | None,
    root: Path | None = None,
) -> dict[str, Any]:
    """Build one ok/mismatch row for the JSON report."""
    try:
        rel = str(path.relative_to(ROOT))
    except ValueError:
        rel = str(path)

    expected: str | None = None
    status = "ok"
    error: str | None = None

    if operator is None:
        status = "mismatch"
        error = "missing_or_unknown_operator"
    elif operator not in CANONICAL_COUPLING:
        status = "mismatch"
        error = f"unknown_operator:{operator}"
    else:
        expected = CANONICAL_COUPLING[operator]
        if actual is None:
            status = "mismatch"
            error = "missing_coupling"
        elif actual not in COUPLINGS:
            status = "mismatch"
            error = f"invalid_coupling:{actual}"
        elif actual != expected:
            status = "mismatch"
            error = f"expected={expected} actual={actual}"

    entry: dict[str, Any] = {
        "path": rel,
        "kind": kind,
        "operator": operator,
        "expected": expected,
        "actual": actual,
        "ok": status == "ok",
        "status": status,
    }
    if error is not None:
        entry["error"] = error
    return entry


def check_ops_specs(ops_root: Path | None = None) -> list[dict[str, Any]]:
    """Verify every ops/<op>/spec.yaml coupling matches the canonical map."""
    root = Path(ops_root) if ops_root is not None else DEFAULT_OPS
    checks: list[dict[str, Any]] = []
    for op in OPERATORS:
        path = root / op.lower() / "spec.yaml"
        if not path.is_file():
            checks.append(
                _check_entry(
                    path=path,
                    kind="ops_spec",
                    operator=op,
                    actual=None,
                )
            )
            # Override error for missing file.
            checks[-1]["error"] = "missing_spec"
            checks[-1]["ok"] = False
            checks[-1]["status"] = "mismatch"
            checks[-1]["expected"] = CANONICAL_COUPLING[op]
            continue
        text = path.read_text(encoding="utf-8")
        parsed_op = _parse_spec_operator(text)
        actual = _parse_spec_coupling(text)
        # Prefer directory/canonical op; flag if YAML operator line disagrees.
        operator = parsed_op if parsed_op in CANONICAL_COUPLING else op
        entry = _check_entry(
            path=path,
            kind="ops_spec",
            operator=operator,
            actual=actual,
        )
        if parsed_op is not None and parsed_op != op:
            entry["ok"] = False
            entry["status"] = "mismatch"
            entry["error"] = f"operator_mismatch:dir={op} yaml={parsed_op}"
            entry["operator"] = op
            entry["expected"] = CANONICAL_COUPLING[op]
        checks.append(entry)
    return checks


def check_cassette_probes(cassette: Path | None = None) -> list[dict[str, Any]]:
    """Verify every cassette **/probe.json coupling matches its operator."""
    root = Path(cassette) if cassette is not None else DEFAULT_CASSETTE
    checks: list[dict[str, Any]] = []
    if not root.is_dir():
        return [
            {
                "path": str(root),
                "kind": "cassette_root",
                "operator": None,
                "expected": None,
                "actual": None,
                "ok": False,
                "status": "mismatch",
                "error": "cassette_root_missing",
            }
        ]

    for path in sorted(root.rglob("probe.json")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            try:
                rel = str(path.relative_to(ROOT))
            except ValueError:
                rel = str(path)
            checks.append(
                {
                    "path": rel,
                    "kind": "cassette_probe",
                    "operator": None,
                    "expected": None,
                    "actual": None,
                    "ok": False,
                    "status": "mismatch",
                    "error": f"unreadable:{exc}",
                }
            )
            continue
        if not isinstance(doc, dict):
            entry = _check_entry(
                path=path,
                kind="cassette_probe",
                operator=None,
                actual=None,
            )
            entry["error"] = "not_an_object"
            checks.append(entry)
            continue
        op = doc.get("operator")
        op_s = str(op) if op is not None else None
        actual = doc.get("coupling")
        actual_s = str(actual) if actual is not None else None
        checks.append(
            _check_entry(
                path=path,
                kind="cassette_probe",
                operator=op_s,
                actual=actual_s,
            )
        )
    return checks


def build_coupling_report(
    *,
    ops_root: Path | None = None,
    cassette: Path | None = None,
) -> dict[str, Any]:
    """Machine-readable coupling-discipline report (ok/mismatch per path)."""
    ops_path = Path(ops_root) if ops_root is not None else DEFAULT_OPS
    cassette_path = Path(cassette) if cassette is not None else DEFAULT_CASSETTE

    checks = check_ops_specs(ops_path) + check_cassette_probes(cassette_path)
    failures = [c["path"] for c in checks if not c.get("ok")]
    # Assert map consistency with package CRN_REQUIRED / OPERATORS.
    map_errors: list[str] = []
    if set(CANONICAL_COUPLING) != set(OPERATORS):
        map_errors.append("canonical_keys != OPERATORS")
    crn_from_map = {op for op, c in CANONICAL_COUPLING.items() if c == "crn_closed_loop"}
    if crn_from_map != set(CRN_REQUIRED):
        map_errors.append("canonical crn_closed_loop set != CRN_REQUIRED")
    if map_errors:
        for msg in map_errors:
            failures.append(f"canonical_map:{msg}")

    ok = not failures and not map_errors
    return {
        "ok": ok,
        "canonical": dict(CANONICAL_COUPLING),
        "ops_root": str(ops_path.resolve()),
        "cassette": str(cassette_path.resolve()),
        "check_count": len(checks),
        "mismatch_count": len([c for c in checks if not c.get("ok")]),
        "checks": checks,
        "failures": failures,
        "map_errors": map_errors,
        "notes": (
            "Coupling discipline: ops/*/spec.yaml and cassette probe.json "
            "must match canonical open_loop vs crn_closed_loop map. "
            "Not AUROC / invented scores."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    """CLI: ``python -m diptych.coupling --check`` / ``diptych-coupling --check``."""
    p = argparse.ArgumentParser(
        prog="diptych-coupling",
        description=(
            "Coupling-discipline gate: verify ops/*/spec.yaml and cassette "
            "probe.json coupling fields against the canonical open_loop / "
            "crn_closed_loop map. Exit non-zero on drift. No invented scores."
        ),
    )
    p.add_argument(
        "--check",
        action="store_true",
        help="Run coupling discipline check (required mode)",
    )
    p.add_argument(
        "--ops",
        type=Path,
        default=DEFAULT_OPS,
        help=f"Ops root with <op>/spec.yaml (default: {DEFAULT_OPS})",
    )
    p.add_argument(
        "--cassette",
        type=Path,
        default=DEFAULT_CASSETTE,
        help=f"Cassette root with **/probe.json (default: {DEFAULT_CASSETTE})",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON report on stdout",
    )
    args = p.parse_args(list(argv) if argv is not None else None)

    if not args.check:
        p.error("pass --check (only supported mode)")

    report = build_coupling_report(ops_root=args.ops, cassette=args.cassette)
    if args.json:
        sys.stdout.write(json.dumps(report, indent=2) + "\n")
    else:
        print("== DIPTYCH coupling-discipline ==")
        print(f"ops={report['ops_root']}")
        print(f"cassette={report['cassette']}")
        print(f"check_count={report['check_count']} mismatch_count={report['mismatch_count']}")
        for entry in report["checks"]:
            status = "OK  " if entry["ok"] else "FAIL"
            line = (
                f"  {status} {entry['path']} "
                f"op={entry.get('operator')} "
                f"expected={entry.get('expected')} actual={entry.get('actual')}"
            )
            if entry.get("error"):
                line += f" — {entry['error']}"
            print(line)
        if report["ok"]:
            print("COUPLING CHECK OK")
        else:
            print(f"COUPLING CHECK FAIL ({', '.join(report['failures'])})")
    return EXIT_OK if report["ok"] else EXIT_FAIL


if __name__ == "__main__":
    raise SystemExit(main())
