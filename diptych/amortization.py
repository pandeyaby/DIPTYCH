"""RQ4 protocol harness: probe-tree amortization counters on cassette controls.

Protocol scaffolding on the **handwritten cassette conforming probes only**
(structural node counts). No AUROC / no model scores / no measured dollar cost or
wall-clock / no empirical RQ4 answer.

1. Reuse ``diptych.probe_tree.prefix_share_amortization`` +
   ``execute_full8_probe_trees`` on cassette conforming probes.
2. Per-op + aggregate counters::

       shared_prefix_nodes
       branch_suffix_nodes
       nodes_naive_two_probes
       nodes_with_prefix_share
       nodes_saved_by_prefix_share

   Protocol factor α = nodes_with_prefix_share / nodes_naive_two_probes
   when naive > 0.
3. Exit non-zero if any probe tree is not ok **or** amortization counters
   are inconsistent (saved != naive − amortized).

Aggregate JSON carries explicit ``non_claims``: protocol definition on
controls, NOT measured dollar cost / NOT empirical RQ4 answer / NOT AUROC.

Stranger path::

    python -m diptych.amortization
    python -m diptych.amortization --json
    diptych-amortization
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable

from diptych import OPERATORS
from diptych.probe_tree import (
    DEFAULT_CASSETTE,
    EXIT_FAIL,
    EXIT_OK,
    execute_full8_probe_trees,
    load_cassette_conforming,
    prefix_share_amortization,
)

ROOT = Path(__file__).resolve().parents[1]

COUNTER_KEYS: tuple[str, ...] = (
    "shared_prefix_nodes",
    "branch_suffix_nodes",
    "nodes_naive_two_probes",
    "nodes_with_prefix_share",
    "nodes_saved_by_prefix_share",
)

NON_CLAIMS: list[str] = [
    "NOT AUROC",
    "NOT measured dollar cost",
    "NOT wall-clock / timing",
    "NOT an empirical RQ4 answer",
    "protocol definition on cassette conforming controls only",
]

NOTES = (
    "RQ4 protocol harness — probe-tree amortization / cost counters on "
    "cassette conforming probes (structural node counts only). Reuses "
    "diptych.probe_tree.prefix_share_amortization + execute_full8_probe_trees. "
    "Per-op and aggregate: shared_prefix_nodes, branch_suffix_nodes, "
    "nodes_naive_two_probes, nodes_with_prefix_share, "
    "nodes_saved_by_prefix_share; protocol factor α = "
    "nodes_with_prefix_share / nodes_naive_two_probes when naive>0. "
    "Fails if any tree not ok or counters inconsistent "
    "(saved != naive - amortized). NOT AUROC / NOT measured dollar cost / NOT "
    "wall-clock / NOT an empirical RQ4 answer."
)


def protocol_alpha(counters: dict[str, Any]) -> float | None:
    """α = nodes_with_prefix_share / nodes_naive_two_probes when naive > 0."""
    try:
        naive = int(counters.get("nodes_naive_two_probes") or 0)
        amortized = int(counters.get("nodes_with_prefix_share") or 0)
    except (TypeError, ValueError):
        return None
    if naive <= 0:
        return None
    return amortized / naive


def counters_consistent(counters: dict[str, Any]) -> bool:
    """True when saved == naive − amortized for structural counters."""
    try:
        naive = int(counters["nodes_naive_two_probes"])
        amortized = int(counters["nodes_with_prefix_share"])
        saved = int(counters["nodes_saved_by_prefix_share"])
    except (KeyError, TypeError, ValueError):
        return False
    return saved == naive - amortized


def _empty_counters() -> dict[str, int]:
    return {k: 0 for k in COUNTER_KEYS}


def _as_int_counters(raw: dict[str, Any] | None) -> dict[str, int]:
    out = _empty_counters()
    if not isinstance(raw, dict):
        return out
    for key in COUNTER_KEYS:
        try:
            out[key] = int(raw.get(key) or 0)
        except (TypeError, ValueError):
            out[key] = 0
    return out


def evaluate_operator_amortization(
    op: str,
    tree: dict[str, Any],
) -> dict[str, Any]:
    """Per-operator structural amortization cell from one probe-tree result."""
    op_u = op.upper()
    am = _as_int_counters(tree.get("amortization") if isinstance(tree, dict) else None)
    # If the tree omitted amortization but carried a horizon, recompute protocol
    # counts via the shared helper (still structural — not measured cost).
    if not any(am.values()) and isinstance(tree, dict) and not tree.get("error"):
        # Prefer helper default for missing amortization (horizon unknown → 0).
        am = _as_int_counters(prefix_share_amortization(0))

    consistent = counters_consistent(am)
    tree_ok = bool(tree.get("ok")) if isinstance(tree, dict) else False
    alpha = protocol_alpha(am)
    return {
        "operator": op_u,
        "ok": tree_ok and consistent and not (tree.get("error") if isinstance(tree, dict) else True),
        "tree_ok": tree_ok,
        "counters_consistent": consistent,
        "amortization": am,
        "alpha": alpha,
        "error": tree.get("error") if isinstance(tree, dict) else "missing_tree",
    }


def aggregate_counters(cells: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Sum per-op structural counters; compute aggregate α."""
    totals = _empty_counters()
    for cell in cells.values():
        am = cell.get("amortization") or {}
        for key in COUNTER_KEYS:
            try:
                totals[key] += int(am.get(key) or 0)
            except (TypeError, ValueError):
                pass
    return {
        "amortization": totals,
        "alpha": protocol_alpha(totals),
        "counters_consistent": counters_consistent(totals),
    }


def build_amortization_report(
    *,
    cassette: Path | None = None,
    load_conf: Callable[[str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Full-8 probe-tree amortization report (protocol scaffolding only)."""
    cassette_path = Path(cassette) if cassette is not None else DEFAULT_CASSETTE
    failures: list[str] = []

    def _load(op: str) -> dict[str, Any]:
        if load_conf is not None:
            return load_conf(op)
        return load_cassette_conforming(op, cassette_path)

    try:
        raw = execute_full8_probe_trees(_load)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        return {
            "ok": False,
            "operator_count": len(OPERATORS),
            "cassette": str(cassette_path),
            "operators": {},
            "aggregate": {
                "amortization": _empty_counters(),
                "alpha": None,
                "counters_consistent": False,
            },
            "failures": [f"execute_failed:{exc}"],
            "notes": NOTES,
            "non_claims": list(NON_CLAIMS),
        }

    operators: dict[str, Any] = {}
    for op in OPERATORS:
        tree = (raw.get("trees") or {}).get(op) or {
            "operator": op,
            "ok": False,
            "error": "missing",
            "amortization": _empty_counters(),
        }
        cell = evaluate_operator_amortization(op, tree)
        operators[op] = cell
        if not cell.get("tree_ok"):
            failures.append(f"{op}:tree_not_ok")
        if not cell.get("counters_consistent"):
            failures.append(f"{op}:amortization_inconsistent")
        if cell.get("error"):
            failures.append(f"{op}:error")

    agg = aggregate_counters(operators)
    if not agg.get("counters_consistent"):
        failures.append("aggregate:amortization_inconsistent")

    trees_ok = bool(raw.get("ok")) and all(
        operators[op].get("tree_ok") for op in OPERATORS
    )
    counters_ok = all(operators[op].get("counters_consistent") for op in OPERATORS) and bool(
        agg.get("counters_consistent")
    )
    ok = trees_ok and counters_ok and not failures

    return {
        "ok": ok,
        "operator_count": len(OPERATORS),
        "cassette": str(cassette_path.resolve()),
        "operators": operators,
        "aggregate": agg,
        "failures": failures,
        "notes": NOTES,
        "non_claims": list(NON_CLAIMS),
    }


def main(argv: list[str] | None = None) -> int:
    """CLI: ``python -m diptych.amortization`` / ``diptych-amortization``."""
    p = argparse.ArgumentParser(
        prog="diptych-amortization",
        description=(
            "RQ4 protocol harness: probe-tree amortization counters on "
            "cassette conforming probes (structural node counts only — "
            "not AUROC / measured dollar cost / wall-clock / empirical RQ4 answer). "
            "Exit non-zero if any tree is not ok or counters are inconsistent."
        ),
    )
    p.add_argument(
        "--cassette",
        type=Path,
        default=DEFAULT_CASSETTE,
        help=(
            "Cassette root with {OP}/conforming/probe.json "
            f"(default: {DEFAULT_CASSETTE})"
        ),
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON report on stdout",
    )
    args = p.parse_args(list(argv) if argv is not None else None)

    report = build_amortization_report(cassette=args.cassette)
    if args.json:
        sys.stdout.write(json.dumps(report, indent=2) + "\n")
    else:
        print("== DIPTYCH amortization (RQ4 protocol harness) ==")
        print(f"cassette={report['cassette']}")
        agg = report.get("aggregate") or {}
        am = agg.get("amortization") or {}
        print(
            f"aggregate: nodes_naive_two_probes={am.get('nodes_naive_two_probes')} "
            f"nodes_with_prefix_share={am.get('nodes_with_prefix_share')} "
            f"nodes_saved_by_prefix_share={am.get('nodes_saved_by_prefix_share')} "
            f"alpha={agg.get('alpha')}"
        )
        print(
            "(structural node counts on cassette conforming controls — "
            "NOT AUROC / measured dollar cost / wall-clock / empirical RQ4 answer)"
        )
        for op, cell in (report.get("operators") or {}).items():
            status = "OK  " if cell.get("ok") else "FAIL"
            cam = cell.get("amortization") or {}
            print(
                f"  {status} {op:12} "
                f"shared={cam.get('shared_prefix_nodes')} "
                f"naive={cam.get('nodes_naive_two_probes')} "
                f"amortized={cam.get('nodes_with_prefix_share')} "
                f"saved={cam.get('nodes_saved_by_prefix_share')} "
                f"alpha={cell.get('alpha')}"
            )
        if report["ok"]:
            print("AMORTIZATION OK")
        else:
            print(f"AMORTIZATION FAIL ({', '.join(report['failures'])})")
    return EXIT_OK if report["ok"] else EXIT_FAIL


if __name__ == "__main__":
    raise SystemExit(main())
