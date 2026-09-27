"""Grader adequacy sweep: exhaustive single-leaf mutation of conforming probes.

For every conforming probe (core cassette, ``diptych-probes``, adapter
fixtures) this module walks every scalar leaf under ``traces[*]`` and applies
each applicable single-leaf mutation:

* number → ``negate`` (``-x``, or ``1.0`` when ``x == 0``) and ``shift``
  (``x + max(1, |x|)``)
* bool → ``toggle``
* string → ``suffix`` (``x + "_mut"``)
* null → ``fill`` (``"mut"``)

Each mutant is classified by *where* it landed and *what the grader said*:

* **on-axis** — the operator's declared graded-axis snapshot
  (``diptych.mutate_axis._axis_snapshot``) changes (or can no longer be
  extracted); **off-axis** otherwise.
* outcome — ``pass`` (mutant survived), ``fail``, ``inconclusive``
  (comparability guard), or ``rejected`` (contract / grader error).

Reported per operator and in aggregate::

    axis_kill_rate      = killed on-axis / on-axis mutants
    off_axis_fail_rate  = off-axis mutants graded fail / off-axis mutants

Surviving on-axis mutants are listed so each one can be audited: a survivor is
either a mutation the predicate is legitimately insensitive to (e.g. a value
still inside its bound) or a grader blind spot. Off-axis ``fail`` verdicts mean
the grader depends on a field outside its declared axis.

These are measured counts over the shipped fixtures — grader adequacy, not
model scores, AUROC, or an empirical RQ answer.

Stranger path::

    python -m diptych.adequacy
    python -m diptych.adequacy --json
    diptych-adequacy
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterator

from diptych import OPERATORS
from diptych.grade import grade_document
from diptych.mutate_axis import _axis_snapshot

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROOTS: tuple[Path, ...] = (
    ROOT / "examples" / "fixtures" / "cassette",
    ROOT / "diptych-probes",
    ROOT / "examples" / "fixtures" / "zeroday",
    ROOT / "examples" / "fixtures" / "aomb",
)
EXIT_OK = 0
EXIT_FAIL = 1

OUTCOMES: tuple[str, ...] = ("pass", "fail", "inconclusive", "rejected")

NON_CLAIMS: list[str] = [
    "NOT AUROC",
    "NOT model scores / ranks",
    "NOT an empirical RQ answer",
    "measured grader adequacy over shipped fixtures only",
]

Path_ = tuple[Any, ...]


def _leaves(node: Any, path: Path_ = ()) -> Iterator[tuple[Path_, Any]]:
    if isinstance(node, dict):
        for k in sorted(node):
            yield from _leaves(node[k], path + (k,))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _leaves(v, path + (i,))
    else:
        yield path, node


def _mutations(value: Any) -> list[tuple[str, Any]]:
    if isinstance(value, bool):
        return [("toggle", not value)]
    if isinstance(value, (int, float)):
        neg = -value if value != 0 else 1.0
        return [("negate", neg), ("shift", value + max(1.0, abs(float(value))))]
    if isinstance(value, str):
        return [("suffix", value + "_mut")]
    if value is None:
        return [("fill", "mut")]
    return []


def _set(doc: dict[str, Any], path: Path_, value: Any) -> None:
    node: Any = doc
    for k in path[:-1]:
        node = node[k]
    node[path[-1]] = value


def _snapshot(op: str, doc: dict[str, Any]) -> Any:
    try:
        return _axis_snapshot(op, doc)
    except Exception as exc:  # noqa: BLE001 — unextractable axis counts as moved
        return ("__unextractable__", type(exc).__name__)


def _verdict(doc: dict[str, Any]) -> str:
    try:
        return grade_document(doc).actual_verdict
    except Exception:  # noqa: BLE001 — contract / grader error
        return "rejected"


def conforming_probes(roots: tuple[Path, ...] = DEFAULT_ROOTS) -> list[Path]:
    out: list[Path] = []
    for root in roots:
        if root.is_dir():
            out.extend(sorted(root.glob("*/conforming/probe.json")))
    return out


def sweep_probe(path: Path) -> dict[str, Any]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    op = str(doc["operator"]).upper()
    base_verdict = _verdict(doc)
    base_axis = _snapshot(op, doc)
    counts: dict[str, Counter[str]] = {"on_axis": Counter(), "off_axis": Counter()}
    survivors: list[dict[str, Any]] = []
    off_axis_fails: list[dict[str, Any]] = []
    for leaf_path, value in _leaves(doc.get("traces", []), ("traces",)):
        for kind, new in _mutations(value):
            mutant = copy.deepcopy(doc)
            _set(mutant, leaf_path, new)
            where = "on_axis" if _snapshot(op, mutant) != base_axis else "off_axis"
            verdict = _verdict(mutant)
            counts[where][verdict] += 1
            rec = {"path": ".".join(map(str, leaf_path)), "mutation": kind}
            if where == "on_axis" and verdict == "pass":
                survivors.append(rec)
            if where == "off_axis" and verdict == "fail":
                off_axis_fails.append(rec)
    return {
        "probe": str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path),
        "operator": op,
        "base_verdict": base_verdict,
        "counts": {w: {o: counts[w][o] for o in OUTCOMES} for w in counts},
        "on_axis_survivors": survivors,
        "off_axis_fails": off_axis_fails,
    }


def _rates(c: dict[str, dict[str, int]]) -> dict[str, Any]:
    on, off = c["on_axis"], c["off_axis"]
    n_on, n_off = sum(on.values()), sum(off.values())
    killed = n_on - on["pass"]
    return {
        "n_on_axis": n_on,
        "n_on_axis_killed": killed,
        "axis_kill_rate": (killed / n_on) if n_on else None,
        "n_off_axis": n_off,
        "n_off_axis_fail": off["fail"],
        "off_axis_fail_rate": (off["fail"] / n_off) if n_off else None,
    }


def _merge(into: dict[str, dict[str, int]], add: dict[str, dict[str, int]]) -> None:
    for w, per in add.items():
        for o, n in per.items():
            into[w][o] += n


def build_adequacy_report(roots: tuple[Path, ...] = DEFAULT_ROOTS) -> dict[str, Any]:
    probes = [sweep_probe(p) for p in conforming_probes(roots)]
    empty = lambda: {w: {o: 0 for o in OUTCOMES} for w in ("on_axis", "off_axis")}  # noqa: E731
    by_op: dict[str, dict[str, Any]] = {}
    total = empty()
    for op in OPERATORS:
        agg = empty()
        rows = [p for p in probes if p["operator"] == op]
        for p in rows:
            _merge(agg, p["counts"])
        _merge(total, agg)
        by_op[op] = {"n_probes": len(rows), "counts": agg, **_rates(agg)}
    bad_base = [p["probe"] for p in probes if p["base_verdict"] != "pass"]
    return {
        "adequacy_schema": "1.0",
        "n_probes": len(probes),
        "base_not_pass": bad_base,
        "aggregate": {"counts": total, **_rates(total)},
        "by_operator": by_op,
        "probes": probes,
        "non_claims": NON_CLAIMS,
        "ok": not bad_base and len(probes) > 0,
    }


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{100 * x:5.1f}%"


def _print_human(report: dict[str, Any]) -> None:
    print("== DIPTYCH adequacy (single-leaf mutation sweep) ==")
    print(f"conforming probes={report['n_probes']}")
    print(f"{'operator':<10} {'probes':>6} {'on-axis':>8} {'killed':>7} {'kill%':>7} "
          f"{'off-axis':>9} {'off-fail':>9} {'off-fail%':>9}")
    rows = list(report["by_operator"].items()) + [("ALL", report["aggregate"])]
    for op, r in rows:
        print(f"{op:<10} {r.get('n_probes', report['n_probes']):>6} {r['n_on_axis']:>8} "
              f"{r['n_on_axis_killed']:>7} {_pct(r['axis_kill_rate']):>7} "
              f"{r['n_off_axis']:>9} {r['n_off_axis_fail']:>9} {_pct(r['off_axis_fail_rate']):>9}")
    for p in report["probes"]:
        for s in p["on_axis_survivors"]:
            print(f"  survivor  {p['operator']:<10} {s['mutation']:<7} {s['path']}  ({p['probe']})")
        for s in p["off_axis_fails"]:
            print(f"  off-fail  {p['operator']:<10} {s['mutation']:<7} {s['path']}  ({p['probe']})")
    if report["base_not_pass"]:
        print(f"base conforming probes not passing: {report['base_not_pass']}")
    print("(measured grader adequacy over shipped fixtures — NOT AUROC / model score)")
    print("ADEQUACY OK" if report["ok"] else "ADEQUACY FAIL")


def main(argv: list[str] | None = None) -> int:
    """CLI: ``python -m diptych.adequacy`` / ``diptych-adequacy``."""
    p = argparse.ArgumentParser(
        prog="diptych-adequacy",
        description="Exhaustive single-leaf mutation sweep over conforming probes.",
    )
    p.add_argument("--root", type=Path, action="append", default=None,
                   help="Fixture root containing <OP>/conforming/probe.json (repeatable)")
    p.add_argument("--json", action="store_true", help="Emit JSON report on stdout")
    args = p.parse_args(argv)
    roots = tuple(args.root) if args.root else DEFAULT_ROOTS
    report = build_adequacy_report(roots)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        _print_human(report)
    return EXIT_OK if report["ok"] else EXIT_FAIL


if __name__ == "__main__":
    sys.exit(main())
