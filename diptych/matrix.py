"""Fixture-derived coverage matrix for ``diptych_core`` (harness-only).

Closes the loop from on-disk CONTRACT fixtures → live ``coverage/matrix.json``
**diptych_core** cells (``green`` + ``axis_power``) using real grade outcomes.

Adapter columns (``zeroday`` / ``aomb``) stay pin-documented truth via
``ADAPTER_PINS`` — never invented from partial ZeroDay/AOMB fixture trees.

Stranger path::

    python -m diptych.matrix --check
    python -m diptych.matrix --write
    python -m diptych.matrix --fixtures examples/fixtures/cassette --check
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from diptych import OPERATORS, SCHEMA
from diptych.contract import ContractError, load_probe
from diptych.gates import MATRIX, SOURCE, _adapter_cell, gate_axis_mutate, write_matrix
from diptych.grade import grade_document

ROOT = Path(__file__).resolve().parents[1]

# Default fixture trees that may contribute diptych_core twin pairs.
# Adapter trees under examples/fixtures/{zeroday,aomb} are intentionally
# excluded — those columns are pins, not fixture-invented greens.
DEFAULT_FIXTURE_ROOTS: tuple[Path, ...] = (
    ROOT / "examples" / "fixtures" / "cassette",
    ROOT / "diptych-probes",
)

CORE_SOURCE = "diptych_core"
ADAPTER_SOURCES = frozenset({"zeroday", "aomb"})


def default_fixture_roots() -> list[Path]:
    """Return existing default fixture roots (cassette + diptych-probes)."""
    return [p for p in DEFAULT_FIXTURE_ROOTS if p.is_dir()]


def discover_core_twin_pairs(
    roots: list[Path] | tuple[Path, ...] | None = None,
) -> dict[str, list[tuple[Path, Path]]]:
    """Map operator → [(conforming, violating), ...] for ``source=diptych_core``.

    Skips inconclusive_* roles and any probe whose ``source`` is an adapter
    (zeroday/aomb). Missing violating twin → pair omitted.
    """
    roots = list(roots) if roots is not None else default_fixture_roots()
    found: dict[str, list[tuple[Path, Path]]] = {op: [] for op in OPERATORS}

    for root in roots:
        root = Path(root)
        if not root.is_dir():
            continue
        for conf_path in sorted(root.rglob("conforming/probe.json")):
            if conf_path.parent.name != "conforming":
                continue
            viol_path = conf_path.parent.parent / "violating" / "probe.json"
            if not viol_path.is_file():
                continue
            try:
                conf = json.loads(conf_path.read_text(encoding="utf-8"))
                viol = json.loads(viol_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(conf, dict) or not isinstance(viol, dict):
                continue
            src = conf.get("source")
            if src in ADAPTER_SOURCES:
                # Never drive matrix cells from adapter fixture trees.
                continue
            if src != CORE_SOURCE:
                continue
            if viol.get("source") != CORE_SOURCE:
                continue
            op = conf.get("operator")
            if op not in OPERATORS or viol.get("operator") != op:
                continue
            pair = (conf_path.resolve(), viol_path.resolve())
            if pair not in found[op]:
                found[op].append(pair)
    return found


def grade_twin_pair(op: str, conf_path: Path, viol_path: Path) -> dict[str, Any]:
    """Grade one conforming/violating twin + axis mutate → cell fields."""
    detail: dict[str, Any] = {
        "operator": op,
        "conforming": str(conf_path),
        "violating": str(viol_path),
        "twin_ok": False,
        "axis_power": False,
        "diptych_core": "pending",
        "error": None,
        "baseline_verdict": None,
        "violating_verdict": None,
        "mutated_verdict": None,
    }
    try:
        conf = load_probe(conf_path)
        viol = load_probe(viol_path)
        g_conf = grade_document(conf)
        g_viol = grade_document(viol)
        failures, power_ev = gate_axis_mutate(op, conf)
    except (ContractError, OSError, KeyError, ValueError, TypeError) as exc:
        detail["error"] = str(exc)
        return detail

    detail["baseline_verdict"] = g_conf.actual_verdict
    detail["violating_verdict"] = g_viol.actual_verdict
    detail["mutated_verdict"] = power_ev.get("mutated_verdict")
    twin_ok = (
        g_conf.matches_expected
        and g_viol.matches_expected
        and g_conf.actual_verdict == "pass"
        and g_viol.actual_verdict == "fail"
    )
    power_ok = power_ev.get("power_ok") is True and not failures
    detail["twin_ok"] = twin_ok
    detail["axis_power"] = power_ok
    detail["diptych_core"] = "green" if (twin_ok and power_ok) else "pending"
    if failures:
        detail["mutate_failures"] = [f.__dict__ for f in failures]
    return detail


def derive_core_cells(
    roots: list[Path] | tuple[Path, ...] | None = None,
) -> dict[str, Any]:
    """Derive ``diptych_core`` + ``axis_power`` for all 8 operators from fixtures.

    An operator is green only when every discovered core twin pair grades green
    with axis power. Adapter fixture trees never contribute.
    """
    resolved_roots = list(roots) if roots is not None else default_fixture_roots()
    pairs = discover_core_twin_pairs(resolved_roots)
    operators: dict[str, Any] = {}
    evidence: dict[str, Any] = {}

    for op in OPERATORS:
        op_pairs = pairs.get(op) or []
        pair_details = [grade_twin_pair(op, c, v) for c, v in op_pairs]
        evidence[op] = {
            "pair_count": len(op_pairs),
            "pairs": pair_details,
        }
        if not pair_details:
            operators[op] = {
                "diptych_core": "pending",
                "axis_power": False,
            }
            continue
        all_green = all(d.get("diptych_core") == "green" for d in pair_details)
        all_power = all(d.get("axis_power") is True for d in pair_details)
        operators[op] = {
            "diptych_core": "green" if all_green else "pending",
            "axis_power": all_power,
        }
    return {
        "diptych_schema": SCHEMA,
        "source_row": SOURCE,
        "operators": operators,
        "evidence": evidence,
        "fixture_roots": [str(Path(p).resolve()) for p in resolved_roots],
    }


def build_matrix_document(
    roots: list[Path] | tuple[Path, ...] | None = None,
    *,
    existing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a full matrix document: fixture-derived core + pin adapter columns.

    Preserves existing ``notes`` when present so ``--write`` does not thrash
    prose relative to ``run_gates`` / PoC refresh.
    """
    derived = derive_core_cells(roots)
    operators: dict[str, Any] = {}
    for op in OPERATORS:
        core = derived["operators"][op]
        operators[op] = {
            "diptych_core": core["diptych_core"],
            # Pin columns only — never grade ZeroDay/AOMB fixture stubs into greens.
            "zeroday": _adapter_cell("zeroday"),
            "aomb": _adapter_cell("aomb"),
            "axis_power": core["axis_power"],
        }

    notes = None
    if existing and isinstance(existing.get("notes"), str):
        notes = existing["notes"]
    if notes is None:
        # Match gates.run_gates note contract (pins + no invented scores).
        notes = 'diptych_core=green requires twin conf/viol AND gate_axis_mutate power-on-axis; zeroday=green and aomb=green at pins ZeroDay@fb5b39daf88e37521aaee8526ae9d286cf74f341 (merged #41+#42) and AOMB@667e47538ae5b9c504187b7a73220d22aa8fb96f (merged #18); no AUROC / invented model scores'

    return {
        "diptych_schema": SCHEMA,
        "source_row": SOURCE,
        "operators": operators,
        "notes": notes,
        "_derive_evidence": derived["evidence"],
        "_fixture_roots": derived["fixture_roots"],
    }


def core_cells_from_matrix(matrix: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Extract comparable diptych_core + axis_power cells from a matrix doc."""
    out: dict[str, dict[str, Any]] = {}
    ops = matrix.get("operators") or {}
    for op in OPERATORS:
        cell = ops.get(op) or {}
        out[op] = {
            "diptych_core": cell.get("diptych_core"),
            "axis_power": cell.get("axis_power"),
        }
    return out


def diff_core_cells(
    live: dict[str, Any],
    derived: dict[str, Any],
) -> list[str]:
    """Return human-readable drift lines for diptych_core / axis_power only."""
    live_core = core_cells_from_matrix(live)
    derived_ops = derived.get("operators") or {}
    # Accept either derive_core_cells() output or a full matrix document.
    derived_core: dict[str, dict[str, Any]] = {}
    for op in OPERATORS:
        cell = derived_ops.get(op) or {}
        derived_core[op] = {
            "diptych_core": cell.get("diptych_core"),
            "axis_power": cell.get("axis_power"),
        }

    errors: list[str] = []
    for op in OPERATORS:
        for key in ("diptych_core", "axis_power"):
            got = live_core[op].get(key)
            want = derived_core[op].get(key)
            if got != want:
                errors.append(f"{op}.{key}: live={got!r} fixture-derived={want!r}")
    return errors


def assert_adapter_pins_untouched_by_fixtures(
    matrix: dict[str, Any],
) -> list[str]:
    """Adapter columns must match pin helper — not partial fixture invention."""
    errors: list[str] = []
    want_z = _adapter_cell("zeroday")
    want_a = _adapter_cell("aomb")
    for op, cell in (matrix.get("operators") or {}).items():
        if cell.get("zeroday") != want_z:
            errors.append(
                f"{op}.zeroday: expected pin cell {want_z!r}, got {cell.get('zeroday')!r}"
            )
        if cell.get("aomb") != want_a:
            errors.append(
                f"{op}.aomb: expected pin cell {want_a!r}, got {cell.get('aomb')!r}"
            )
    return errors


def load_matrix(path: Path | None = None) -> dict[str, Any]:
    path = path or MATRIX
    return json.loads(Path(path).read_text(encoding="utf-8"))


def refresh_matrix(
    path: Path | None = None,
    roots: list[Path] | tuple[Path, ...] | None = None,
    *,
    write: bool = True,
) -> dict[str, Any]:
    """Rebuild matrix from fixtures (core) + pins (adapters); optionally write."""
    path = Path(path) if path is not None else MATRIX
    existing = load_matrix(path) if path.is_file() else None
    doc = build_matrix_document(roots, existing=existing)
    public = {
        "diptych_schema": doc["diptych_schema"],
        "source_row": doc["source_row"],
        "operators": doc["operators"],
        "notes": doc["notes"],
    }
    if write:
        write_matrix(public, path)
    return doc


def verify_matrix(
    path: Path | None = None,
    roots: list[Path] | tuple[Path, ...] | None = None,
) -> list[str]:
    """Compare live matrix diptych_core row to fixture-derived grades."""
    path = Path(path) if path is not None else MATRIX
    live = load_matrix(path)
    derived = derive_core_cells(roots)
    errors = diff_core_cells(live, derived)
    errors.extend(assert_adapter_pins_untouched_by_fixtures(live))
    return errors


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="python -m diptych.matrix",
        description=(
            "Derive/verify coverage/matrix.json diptych_core cells from fixture "
            "grades. Adapter columns stay pin-documented (no invented ZeroDay/AOMB greens)."
        ),
    )
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--check",
        action="store_true",
        help="Fail if live matrix diptych_core/axis_power drifts from fixture grades",
    )
    mode.add_argument(
        "--write",
        action="store_true",
        help=(
            "Rewrite coverage/matrix.json diptych_core+axis_power from fixtures; "
            "adapter columns remain pin-documented"
        ),
    )
    p.add_argument(
        "--fixtures",
        action="append",
        type=Path,
        default=None,
        help=(
            "Fixture root to grade (repeatable). Default: "
            "examples/fixtures/cassette + diptych-probes"
        ),
    )
    p.add_argument(
        "--matrix",
        type=Path,
        default=MATRIX,
        help=f"Path to matrix.json (default: {MATRIX})",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON report on stdout",
    )
    args = p.parse_args(argv)
    roots = args.fixtures if args.fixtures else None

    if args.write:
        doc = refresh_matrix(args.matrix, roots, write=True)
        core = {op: doc["operators"][op] for op in OPERATORS}
        report = {
            "ok": all(c.get("diptych_core") == "green" for c in core.values()),
            "mode": "write",
            "matrix": str(args.matrix),
            "fixture_roots": doc.get("_fixture_roots"),
            "operators": {
                op: {
                    "diptych_core": core[op]["diptych_core"],
                    "axis_power": core[op]["axis_power"],
                    "zeroday": core[op]["zeroday"],
                    "aomb": core[op]["aomb"],
                }
                for op in OPERATORS
            },
        }
        if args.json:
            sys.stdout.write(json.dumps(report, indent=2) + "\n")
        else:
            print(
                f"wrote {args.matrix} "
                "(diptych_core from fixtures; zeroday/aomb adapter pins preserved)"
            )
            for op in OPERATORS:
                c = core[op]
                print(
                    f"  {op:12} diptych_core={c['diptych_core']} "
                    f"axis_power={c['axis_power']} "
                    f"zeroday={c['zeroday']}(pin) aomb={c['aomb']}(pin)"
                )
            print("MATRIX WRITE", "OK" if report["ok"] else "PENDING")
        return 0 if report["ok"] else 1

    # --check
    errors = verify_matrix(args.matrix, roots)
    derived = derive_core_cells(roots)
    report = {
        "ok": not errors,
        "mode": "check",
        "matrix": str(args.matrix),
        "fixture_roots": derived["fixture_roots"],
        "errors": errors,
        "fixture_derived": derived["operators"],
    }
    if args.json:
        sys.stdout.write(json.dumps(report, indent=2) + "\n")
    else:
        if errors:
            print("MATRIX DRIFT (diptych_core vs fixture grades):")
            for e in errors:
                print(f"  {e}")
            print("MATRIX CHECK FAIL")
        else:
            print(
                "MATRIX CHECK OK "
                "(diptych_core+axis_power match fixture grades; "
                "zeroday/aomb columns are pins)"
            )
            for op in OPERATORS:
                c = derived["operators"][op]
                print(
                    f"  {op:12} diptych_core={c['diptych_core']} "
                    f"axis_power={c['axis_power']}"
                )
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
