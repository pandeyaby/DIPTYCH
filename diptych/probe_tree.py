"""Probe-tree execution for mutate-axis power (harness-only).

Each operator's conforming probe is a tree root. The single mutate-axis
branch applies the named-axis mutator and records a semantic witness.
Wrong-axis / cosmetic branches are negative controls, not green leaves.
"""

from __future__ import annotations

from typing import Any, Callable

from diptych import OPERATORS
from diptych.contract import ContractError, validate_envelope
from diptych.grade import grade_document
from diptych.mutate_axis import (
    AXIS_SPECS,
    MUTATION_DESCRIPTIONS,
    build_semantic_witness,
    mutate_axis,
    mutate_wrong_axis,
)



def prefix_share_amortization(horizon_length: int) -> dict[str, int]:
    """Protocol node counts for shared-prefix amortization (not $ or timing).

    A conforming root spine of ``horizon_length`` nodes is shared by the
    mutate-axis and wrong-axis branches; only the branch suffixes are unique.
    These are structural counters for the probe-tree protocol — not measured
    wall-clock, dollars, or invented performance claims.
    """
    shared = max(int(horizon_length), 0)
    branch_suffixes = 2  # mutate_axis + wrong_axis
    naive = 2 * shared  # two full probes without sharing
    amortized = shared + branch_suffixes
    return {
        "shared_prefix_nodes": shared,
        "branch_suffix_nodes": branch_suffixes,
        "nodes_naive_two_probes": naive,
        "nodes_with_prefix_share": amortized,
        "nodes_saved_by_prefix_share": max(naive - amortized, 0),
    }


def execute_mutate_axis_branch(
    op: str,
    conf: dict[str, Any],
    *,
    mutator: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    claimed_axis: str | None = None,
) -> dict[str, Any]:
    """Run baseline → mutate-axis branch; return structured tree node evidence."""
    op = op.upper()
    if op not in OPERATORS:
        raise KeyError(f"unknown operator {op!r}")

    node: dict[str, Any] = {
        "operator": op,
        "expected_axis": AXIS_SPECS[op]["expected_axis"],
        "channel": AXIS_SPECS[op]["channel"],
        "mutation": MUTATION_DESCRIPTIONS.get(op, ""),
        "baseline": {"verdict": None, "reason": None},
        "mutated": {"verdict": None, "reason": None},
        "semantic_witness": None,
        "power_ok": False,
        "branch": "mutate_axis",
    }

    validate_envelope(conf)
    base = grade_document(conf)
    node["baseline"] = {"verdict": base.actual_verdict, "reason": base.reason}
    if base.actual_verdict != "pass":
        node["error"] = "baseline_must_pass"
        return node

    apply = mutator or (lambda d: mutate_axis(d, op))
    mutated = apply(conf)
    claimed = claimed_axis
    if claimed is None:
        claimed = (mutated.get("meta") or {}).get("claimed_axis")
    witness = build_semantic_witness(op, conf, mutated, claimed_axis=claimed)
    node["semantic_witness"] = witness.to_dict()

    mutated.setdefault("control_role", conf.get("control_role", "conforming"))
    mutated.setdefault("expected_verdict", conf.get("expected_verdict", "pass"))
    if op in ("TRAJSWAP", "VARSCALE") and mutated.get("coupling") != "crn_closed_loop":
        node["error"] = "dropped_crn_closed_loop"
        return node

    validate_envelope(mutated)
    after = grade_document(mutated)
    node["mutated"] = {"verdict": after.actual_verdict, "reason": after.reason}

    node["power_ok"] = bool(
        witness.axis_match
        and witness.changed
        and base.actual_verdict == "pass"
        and after.actual_verdict == "fail"
    )
    return node


def execute_wrong_axis_branch(op: str, conf: dict[str, Any]) -> dict[str, Any]:
    """Negative control: off-axis distractor must not flip conforming→fail."""
    op = op.upper()
    validate_envelope(conf)
    base = grade_document(conf)
    mutated = mutate_wrong_axis(conf, op)
    claimed = (mutated.get("meta") or {}).get("claimed_axis")
    witness = build_semantic_witness(op, conf, mutated, claimed_axis=claimed)
    mutated.setdefault("control_role", conf.get("control_role", "conforming"))
    mutated.setdefault("expected_verdict", conf.get("expected_verdict", "pass"))
    validate_envelope(mutated)
    after = grade_document(mutated)
    return {
        "operator": op,
        "branch": "wrong_axis",
        "expected_axis": AXIS_SPECS[op]["expected_axis"],
        "baseline_verdict": base.actual_verdict,
        "mutated_verdict": after.actual_verdict,
        "falsely_flipped": base.actual_verdict == "pass" and after.actual_verdict == "fail",
        "semantic_witness": witness.to_dict(),
        # Wrong-axis power must be rejected (axis_match False and/or no flip).
        "power_ok": False,
        "axis_intact": not witness.changed,
    }


def execute_operator_probe_tree(op: str, conf: dict[str, Any]) -> dict[str, Any]:
    """Full probe-tree for one operator: correct-axis + wrong-axis branches."""
    correct = execute_mutate_axis_branch(op, conf)
    wrong = execute_wrong_axis_branch(op, conf)
    horizon = conf.get("horizon") if isinstance(conf.get("horizon"), dict) else {}
    length = horizon.get("length", 0)
    try:
        length_i = int(length)
    except (TypeError, ValueError):
        length_i = 0
    return {
        "operator": op,
        "root": "conforming",
        "branches": {
            "mutate_axis": correct,
            "wrong_axis": wrong,
        },
        "amortization": prefix_share_amortization(length_i),
        "ok": bool(correct.get("power_ok")) and not wrong.get("falsely_flipped"),
    }



def execute_full8_probe_trees(
    load_conf: Callable[[str], dict[str, Any]],
) -> dict[str, Any]:
    """Run mutate-axis probe trees for all 8 operators."""
    trees: dict[str, Any] = {}
    for op in OPERATORS:
        try:
            trees[op] = execute_operator_probe_tree(op, load_conf(op))
        except (ContractError, KeyError, AssertionError, ValueError) as e:
            trees[op] = {"operator": op, "ok": False, "error": str(e)}
    return {
        "operator_count": len(OPERATORS),
        "trees": trees,
        "ok": all(t.get("ok") for t in trees.values()),
    }


# ---------------------------------------------------------------------------
# Stranger CLI: full-8 probe trees from cassette conforming probes
# ---------------------------------------------------------------------------

import argparse
import json
import sys
from pathlib import Path

from diptych.contract import load_probe

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASSETTE = ROOT / "examples" / "fixtures" / "cassette"

EXIT_OK = 0
EXIT_FAIL = 1


def load_cassette_conforming(op: str, cassette: Path | None = None) -> dict:
    """Load ``{cassette}/{OP}/conforming/probe.json`` (diptych_core harness)."""
    root = Path(cassette) if cassette is not None else DEFAULT_CASSETTE
    path = root / op.upper() / "conforming" / "probe.json"
    return load_probe(path)


def build_probe_tree_report(
    *,
    cassette: Path | None = None,
) -> dict:
    """Machine-readable full-8 probe-tree report (mutate-axis + wrong-axis).

    Amortization fields are structural node counts only — not $ or timing.
    """
    cassette_path = Path(cassette) if cassette is not None else DEFAULT_CASSETTE

    def load_conf(op: str) -> dict:
        return load_cassette_conforming(op, cassette_path)

    raw = execute_full8_probe_trees(load_conf)
    operators: dict[str, Any] = {}
    failures: list[str] = []
    for op in OPERATORS:
        tree = raw["trees"].get(op) or {"operator": op, "ok": False, "error": "missing"}
        mutate = (tree.get("branches") or {}).get("mutate_axis") or {}
        wrong = (tree.get("branches") or {}).get("wrong_axis") or {}
        power_ok = bool(mutate.get("power_ok"))
        falsely = bool(wrong.get("falsely_flipped"))
        op_ok = bool(tree.get("ok")) and power_ok and not falsely
        if not power_ok:
            failures.append(f"{op}:mutate_axis.power_ok")
        if falsely:
            failures.append(f"{op}:wrong_axis.falsely_flipped")
        if tree.get("error"):
            failures.append(f"{op}:error")
        operators[op] = {
            "ok": op_ok,
            "mutate_axis": {
                "power_ok": power_ok,
                "baseline_verdict": (mutate.get("baseline") or {}).get("verdict"),
                "mutated_verdict": (mutate.get("mutated") or {}).get("verdict"),
            },
            "wrong_axis": {
                "falsely_flipped": falsely,
                "baseline_verdict": wrong.get("baseline_verdict"),
                "mutated_verdict": wrong.get("mutated_verdict"),
                "axis_intact": wrong.get("axis_intact"),
            },
            "amortization": tree.get("amortization") or {},
            "error": tree.get("error"),
        }

    ok = bool(raw.get("ok")) and not failures
    return {
        "ok": ok,
        "operator_count": len(OPERATORS),
        "cassette": str(cassette_path.resolve()),
        "operators": operators,
        "failures": failures,
        "notes": (
            "Probe-tree: mutate-axis must flip conforming→fail (power_ok); "
            "wrong-axis must not falsely flip pass→fail. "
            "Amortization = structural shared-prefix node counts only "
            "(not $, timing, or invented scores)."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    """CLI: ``python -m diptych.probe_tree`` / ``diptych-probe-tree``."""
    p = argparse.ArgumentParser(
        prog="diptych-probe-tree",
        description=(
            "Run full-8 probe trees from cassette conforming probes: "
            "mutate-axis power + wrong-axis negative controls. "
            "Amortization counters are structural only (not $ / timing)."
        ),
    )
    p.add_argument(
        "--cassette",
        type=Path,
        default=DEFAULT_CASSETTE,
        help=f"Cassette root with {{OP}}/conforming/probe.json (default: {DEFAULT_CASSETTE})",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON report on stdout",
    )
    args = p.parse_args(list(argv) if argv is not None else None)

    report = build_probe_tree_report(cassette=args.cassette)
    if args.json:
        sys.stdout.write(json.dumps(report, indent=2) + "\n")
    else:
        print("== DIPTYCH probe-tree ==")
        print(f"cassette={report['cassette']}")
        print(f"operator_count={report['operator_count']}")
        for op, cell in report["operators"].items():
            status = "OK  " if cell["ok"] else "FAIL"
            ma = cell["mutate_axis"]
            wa = cell["wrong_axis"]
            print(
                f"  {status} {op:12} "
                f"mutate_axis.power_ok={ma['power_ok']} "
                f"wrong_axis.falsely_flipped={wa['falsely_flipped']}"
            )
            am = cell.get("amortization") or {}
            if am:
                print(
                    f"           amortization nodes_with_prefix_share="
                    f"{am.get('nodes_with_prefix_share')} "
                    f"nodes_saved={am.get('nodes_saved_by_prefix_share')}"
                )
            if cell.get("error"):
                print(f"           error={cell['error']}")
        if report["ok"]:
            print("PROBE-TREE OK")
        else:
            print(f"PROBE-TREE FAIL ({', '.join(report['failures'])})")
    return EXIT_OK if report["ok"] else EXIT_FAIL


if __name__ == "__main__":
    raise SystemExit(main())
