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
    return {
        "operator": op,
        "root": "conforming",
        "branches": {
            "mutate_axis": correct,
            "wrong_axis": wrong,
        },
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
