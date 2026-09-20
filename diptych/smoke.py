"""Unified stranger smoke: one command exercises the public harness surface.

Runs in order (exit non-zero on any failure)::

    1. schema-check freshness (CONTRACT JSON Schema ↔ live enums)
    2. grade cassette fixtures (JSON)
    3. matrix --check (diptych_core from fixtures)
    4. poc --json (full-8 structured report)
    5. thin corpus reject sanity (must reject loudly)

Machine-readable report via ``--json``. No AUROC / invented model scores.

Stranger path::

    pip install -e ".[dev]"
    diptych
    diptych smoke
    python -m diptych          # still works
    make smoke
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable

from diptych import OPERATORS, SCHEMA
from diptych.grade import build_grade_report
from diptych.matrix import verify_matrix
from diptych.poc import POC_SCHEMA, build_poc_report
from diptych.schema import SCHEMA_JSON_RELPATH, SchemaError, assert_committed_schema_fresh

SMOKE_SCHEMA = "1.0"

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASSETTE = ROOT / "examples" / "fixtures" / "cassette"
DEFAULT_THIN = ROOT / "tests" / "fixtures" / "thin"
DEFAULT_JSON_OUT = ROOT / "reports" / "paired-probes" / "smoke_report.json"

# Stable step ids (order = run order).
STEP_SCHEMA_FRESH = "schema_fresh"
STEP_GRADE_CASSETTE = "grade_cassette"
STEP_MATRIX_CHECK = "matrix_check"
STEP_POC_JSON = "poc_json"
STEP_THIN_REJECT = "thin_reject"

SMOKE_STEP_IDS: tuple[str, ...] = (
    STEP_SCHEMA_FRESH,
    STEP_GRADE_CASSETTE,
    STEP_MATRIX_CHECK,
    STEP_POC_JSON,
    STEP_THIN_REJECT,
)

SMOKE_REQUIRED_KEYS = (
    "smoke_schema",
    "diptych_schema",
    "ok",
    "steps",
    "failures",
    "step_count",
)


def _step(
    step_id: str,
    *,
    ok: bool,
    detail: dict[str, Any] | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    entry: dict[str, Any] = {"id": step_id, "ok": bool(ok)}
    if detail is not None:
        entry["detail"] = detail
    if error is not None:
        entry["error"] = error
    return entry


def _run_schema_fresh() -> dict[str, Any]:
    try:
        assert_committed_schema_fresh()
    except SchemaError as exc:
        return _step(
            STEP_SCHEMA_FRESH,
            ok=False,
            detail={"path": str(SCHEMA_JSON_RELPATH)},
            error=str(exc),
        )
    return _step(
        STEP_SCHEMA_FRESH,
        ok=True,
        detail={"path": str(SCHEMA_JSON_RELPATH), "status": "fresh"},
    )


def _run_grade_cassette(cassette: Path) -> dict[str, Any]:
    if not cassette.exists():
        return _step(
            STEP_GRADE_CASSETTE,
            ok=False,
            detail={"input": str(cassette)},
            error=f"cassette path missing: {cassette}",
        )
    report = build_grade_report(cassette)
    detail = {
        "input": str(cassette.resolve()),
        "ok": report.get("ok"),
        "count": report.get("count"),
        "graded": report.get("graded"),
        "rejected": report.get("rejected"),
        "mismatches": report.get("mismatches"),
    }
    ok = bool(report.get("ok"))
    error = None
    if not ok:
        error = (
            f"grade cassette failed: graded={detail['graded']} "
            f"rejected={detail['rejected']} mismatches={detail['mismatches']}"
        )
    return _step(STEP_GRADE_CASSETTE, ok=ok, detail=detail, error=error)


def _run_matrix_check() -> dict[str, Any]:
    errors = verify_matrix()
    detail: dict[str, Any] = {
        "error_count": len(errors),
        "errors": list(errors),
    }
    if errors:
        return _step(
            STEP_MATRIX_CHECK,
            ok=False,
            detail=detail,
            error=f"matrix check failed ({len(errors)} drift error(s))",
        )
    return _step(STEP_MATRIX_CHECK, ok=True, detail=detail)


def _run_poc_json() -> dict[str, Any]:
    poc = build_poc_report()
    detail = {
        "poc_schema": poc.get("poc_schema", POC_SCHEMA),
        "ok": poc.get("ok"),
        "operator_count": poc.get("operator_count"),
        "failure_count": len(poc.get("failures") or []),
    }
    ok = bool(poc.get("ok")) and int(poc.get("operator_count") or 0) == len(OPERATORS)
    error = None
    if not ok:
        failures = poc.get("failures") or []
        error = f"poc json failed: failures={failures!r}"
    return _step(STEP_POC_JSON, ok=ok, detail=detail, error=error)


def _run_thin_reject(thin: Path) -> dict[str, Any]:
    """Thin corpus must reject loudly — smoke pass = all rejected, none graded ok."""
    if not thin.exists():
        return _step(
            STEP_THIN_REJECT,
            ok=False,
            detail={"input": str(thin)},
            error=f"thin corpus path missing: {thin}",
        )
    report = build_grade_report(thin)
    count = int(report.get("count") or 0)
    rejected = int(report.get("rejected") or 0)
    graded = int(report.get("graded") or 0)
    # Success condition is inverted vs grade: expect full reject, zero graded.
    ok = count > 0 and rejected == count and graded == 0 and not report.get("ok")
    detail = {
        "input": str(thin.resolve()),
        "count": count,
        "rejected": rejected,
        "graded": graded,
        "grade_ok": report.get("ok"),
        "expect": "all_rejected",
    }
    error = None
    if not ok:
        error = (
            f"thin reject sanity failed: expected all rejected, "
            f"got count={count} rejected={rejected} graded={graded}"
        )
    return _step(STEP_THIN_REJECT, ok=ok, detail=detail, error=error)


def run_smoke(
    *,
    cassette: Path | None = None,
    thin: Path | None = None,
) -> dict[str, Any]:
    """Execute the full public-surface smoke sequence → report dict."""
    cassette_path = Path(cassette) if cassette is not None else DEFAULT_CASSETTE
    thin_path = Path(thin) if thin is not None else DEFAULT_THIN

    runners: list[tuple[str, Callable[[], dict[str, Any]]]] = [
        (STEP_SCHEMA_FRESH, _run_schema_fresh),
        (STEP_GRADE_CASSETTE, lambda: _run_grade_cassette(cassette_path)),
        (STEP_MATRIX_CHECK, _run_matrix_check),
        (STEP_POC_JSON, _run_poc_json),
        (STEP_THIN_REJECT, lambda: _run_thin_reject(thin_path)),
    ]

    steps: list[dict[str, Any]] = []
    for _step_id, runner in runners:
        steps.append(runner())

    failures = [s["id"] for s in steps if not s.get("ok")]
    return {
        "smoke_schema": SMOKE_SCHEMA,
        "diptych_schema": SCHEMA,
        "ok": not failures,
        "step_count": len(steps),
        "steps": steps,
        "failures": failures,
        "cassette": str(cassette_path.resolve()),
        "thin": str(thin_path.resolve()),
        "notes": (
            "Smoke proves harness public surface (schema freshness, cassette grade, "
            "matrix check, poc json, thin reject). Not AUROC / accuracy / vuln-finding."
        ),
    }


def _print_human(report: dict[str, Any]) -> None:
    print("== DIPTYCH smoke ==")
    print(f"diptych_schema={report.get('diptych_schema')} smoke_schema={report.get('smoke_schema')}")
    for step in report.get("steps") or []:
        status = "OK  " if step.get("ok") else "FAIL"
        line = f"  {status} {step.get('id')}"
        if step.get("error"):
            line += f" — {step['error']}"
        elif isinstance(step.get("detail"), dict):
            d = step["detail"]
            # Compact, no fake metrics — only counts / status already measured.
            bits: list[str] = []
            for key in ("status", "count", "graded", "rejected", "operator_count", "error_count"):
                if key in d:
                    bits.append(f"{key}={d[key]}")
            if bits:
                line += " (" + ", ".join(bits) + ")"
        print(line)
    if report.get("ok"):
        print("SMOKE OK")
    else:
        print(f"SMOKE FAIL ({', '.join(report.get('failures') or [])})")


def _cli_prog() -> str:
    """Prefer console-script name when installed; else ``python -m diptych``."""
    name = Path(sys.argv[0]).name
    if name in ("diptych", "diptych.exe"):
        return "diptych"
    return "python -m diptych"


def main(argv: list[str] | None = None) -> int:
    """CLI: ``diptych`` / ``diptych smoke`` / ``python -m diptych``."""
    p = argparse.ArgumentParser(
        prog=_cli_prog(),
        description=(
            "DIPTYCH unified stranger smoke: schema freshness, cassette grade, "
            "matrix check, poc json, thin reject. Stdlib-only; no invented scores."
        ),
    )
    p.add_argument(
        "command",
        nargs="?",
        default="smoke",
        choices=("smoke",),
        help="Subcommand (default: smoke)",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable smoke report JSON",
    )
    p.add_argument(
        "--out",
        type=Path,
        default=None,
        help=f"Write smoke JSON to path (default: {DEFAULT_JSON_OUT} when --json)",
    )
    p.add_argument(
        "--stdout-only",
        action="store_true",
        help="With --json: print JSON to stdout only (no file write)",
    )
    p.add_argument(
        "--cassette",
        type=Path,
        default=DEFAULT_CASSETTE,
        help=f"Cassette fixture root (default: {DEFAULT_CASSETTE})",
    )
    p.add_argument(
        "--thin",
        type=Path,
        default=DEFAULT_THIN,
        help=f"Thin reject corpus root (default: {DEFAULT_THIN})",
    )
    p.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress human table (JSON still emitted when requested)",
    )
    args = p.parse_args(argv)

    # command currently only "smoke"; reserved for future top-level subcommands.
    _ = args.command

    report = run_smoke(cassette=args.cassette, thin=args.thin)
    emit_json = args.json or args.stdout_only or args.out is not None

    if not args.quiet and not args.stdout_only:
        _print_human(report)

    if emit_json:
        text = json.dumps(report, indent=2) + "\n"
        if args.stdout_only:
            sys.stdout.write(text)
        else:
            out = args.out or DEFAULT_JSON_OUT
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(text, encoding="utf-8")
            if not args.quiet:
                print(f"json -> {out}")

    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
