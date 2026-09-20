"""DIPTYCH gates for AOMB (GATING.md / CONTRACT.md).

Fails on: incomplete manifest, missing violating twin, identical twins,
asymmetric-verdict failure, stub markers, wrong coupling on CRN ops,
and missing axis-power (gate_axis_mutate) flips.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from diptych import ADAPTER_PINS, CRN_REQUIRED, OPERATORS, SCHEMA, SOURCES, STUB_MARKERS
from diptych.contract import ContractError, load_probe, validate_envelope
from diptych.grade import GradeResult, grade_document
from diptych.mutate_axis import (
    AXIS_SPECS,
    MUTATION_DESCRIPTIONS,
    axis_fingerprint,
    build_semantic_witness,
    mutate_axis,
)
from diptych.probe_tree import execute_mutate_axis_branch

# Adapter columns are green at these pins only (merge facts, not invented scores):
# ZeroDay fb5b39da = merged #41+#42; AOMB 667e475 = merged #18. Product trees stay out of repo.
_ADAPTER_GREEN_PINS = {
    "zeroday": "fb5b39daf88e37521aaee8526ae9d286cf74f341",
    "aomb": "667e47538ae5b9c504187b7a73220d22aa8fb96f",
}


def _adapter_cell(source: str) -> str:
    return "green" if ADAPTER_PINS.get(source) == _ADAPTER_GREEN_PINS.get(source) else "pending"

ROOT = Path(__file__).resolve().parents[1]
PROBES = ROOT / "diptych-probes"
SOURCE = "diptych_core"
MATRIX = ROOT / "coverage" / "matrix.json"


@dataclass
class Failure:
    gate: str
    detail: str


@dataclass
class Report:
    ok: bool
    failures: list[Failure] = field(default_factory=list)
    results: list[dict[str, Any]] = field(default_factory=list)
    matrix: dict[str, Any] = field(default_factory=dict)
    axis_power: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "failures": [f.__dict__ for f in self.failures],
            "results": self.results,
            "matrix": self.matrix,
            "axis_power": self.axis_power,
        }


def _path(op: str, role: str) -> Path:
    return PROBES / op / role / "probe.json"


def gate_manifest() -> list[Failure]:
    out: list[Failure] = []
    if not PROBES.is_dir():
        return [Failure("manifest", f"missing {PROBES}")]
    for op in OPERATORS:
        for role in ("conforming", "violating"):
            p = _path(op, role)
            if not p.is_file():
                out.append(Failure("manifest", f"missing {p.relative_to(ROOT)}"))
    return out


def gate_stubs(paths: list[Path]) -> list[Failure]:
    out: list[Failure] = []
    for path in paths:
        text = path.read_text(encoding="utf-8")
        doc = json.loads(text)
        if not doc.get("traces") or len(doc["traces"]) < 2:
            out.append(Failure("stub", f"{path.relative_to(ROOT)} empty/short traces"))
        if path.parent.name == "violating" and doc.get("expected_verdict") == "pass":
            out.append(Failure("stub", f"{path.relative_to(ROOT)} violating expected pass"))
        if path.parent.name == "conforming" and doc.get("expected_verdict") == "fail":
            out.append(Failure("stub", f"{path.relative_to(ROOT)} conforming expected fail"))
        for marker in ("TODO", "NotImplemented", "STUB_OPERATOR", "hardcoded_pass"):
            if f'"{marker}"' in text or f": {marker}" in text:
                out.append(Failure("stub", f"{path.relative_to(ROOT)} contains stub token {marker!r}"))
    for path in (ROOT / "eval" / "diptych").glob("*.py"):
        if path.name in {"gates.py", "__init__.py", "mutate_axis.py"}:
            continue
        src = path.read_text(encoding="utf-8")
        if "raise NotImplementedError" in src or "raise NotImplemented" in src:
            out.append(Failure("stub", f"{path.relative_to(ROOT)} raises NotImplemented"))
        if re.search(r"return True\s*#\s*(stub|hardcoded|TODO)", src):
            out.append(Failure("stub", f"{path.relative_to(ROOT)} hardcoded True stub"))
        if re.search(r"def grade_\w+\([^)]*\):\s*\.\.\.", src):
            out.append(Failure("stub", f"{path.relative_to(ROOT)} ellipsis grader"))
    return out


def gate_contrast(op: str, conf: dict, viol: dict) -> list[Failure]:
    out: list[Failure] = []
    if json.dumps(conf["traces"], sort_keys=True) == json.dumps(viol["traces"], sort_keys=True):
        out.append(Failure("contrast", f"{op}: identical twins (no axis mutation)"))
    if conf["expected_verdict"] == viol["expected_verdict"]:
        out.append(Failure("contrast", f"{op}: expected_verdict not asymmetric"))
    return out


def gate_axis(op: str, conf: dict, viol: dict) -> list[Failure]:
    out: list[Failure] = []
    if op in CRN_REQUIRED:
        for role, doc in (("conforming", conf), ("violating", viol)):
            if doc.get("coupling") != "crn_closed_loop":
                out.append(Failure("axis", f"{op}/{role}: coupling must be crn_closed_loop"))
    checks = {
        "RESEED": lambda d: (
            "stability" in d["traces"][0]["channels"]
            and "epsilon" in d["traces"][0]["meta"]
            and d["traces"][0]["meta"].get("seed") != d["traces"][1]["meta"].get("seed")
        ),
        "SCHEMAX": lambda d: "schema" in d["traces"][0]["channels"]
        and isinstance(d["traces"][0]["channels"]["schema"].get("keys"), list),
        "FREEZEDRY": lambda d: (
            "graded" in d["traces"][0]["channels"]
            and "freeze_channels" in d["traces"][0]["meta"]
            and "decision_fingerprint" in d["traces"][0]["meta"]
        ),
        "SIGNFLIP": lambda d: "signflip_channel" in d["traces"][0]["meta"],
        "SATEXTEND": lambda d: "sat_lo" in d["traces"][0]["meta"] and "sat_hi" in d["traces"][0]["meta"],
        "HISTSWAP": lambda d: (
            "history" in d["traces"][0]["channels"]
            and "hist_splice_at" in d["traces"][0]["meta"]
        ),
        "TRAJSWAP": lambda d: (
            "trajectory" in d["traces"][0]["channels"]
            and "closed_loop_residual" in d["traces"][0]["channels"]
        ),
        "VARSCALE": lambda d: (
            "variance_proxy" in d["traces"][0]["channels"]
            and "var_scale" in d["traces"][0]["meta"]
        ),
    }
    fn = checks[op]
    for role, doc in (("conforming", conf), ("violating", viol)):
        try:
            if not fn(doc):
                out.append(Failure("axis", f"{op}/{role}: missing axis fields"))
        except Exception as e:  # noqa: BLE001
            out.append(Failure("axis", f"{op}/{role}: {e}"))
    return out


def gate_axis_mutate(
    op: str,
    conf: dict[str, Any],
    *,
    mutator: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> tuple[list[Failure], dict[str, Any]]:
    """Power-on-axis: mutate only the operator axis on conforming; grader must flip.

    Requires a semantic witness (expected_axis + channel + before/after) matching
    AXIS_SPECS[op]. Rejects cosmetic edits and wrong-axis distractors.
    """
    failures: list[Failure] = []
    spec = AXIS_SPECS.get(op, {})
    evidence: dict[str, Any] = {
        "operator": op,
        "mutation": MUTATION_DESCRIPTIONS.get(op, ""),
        "expected_axis": spec.get("expected_axis"),
        "channel": spec.get("channel"),
        "baseline_verdict": None,
        "mutated_verdict": None,
        "axis_changed": False,
        "power_ok": False,
        "semantic_witness": None,
        "probe_tree_branch": None,
    }
    try:
        validate_envelope(conf)
        base = grade_document(conf)
    except ContractError as e:
        failures.append(Failure("axis_mutate", f"{op}: conforming grade error: {e}"))
        return failures, evidence

    evidence["baseline_verdict"] = base.actual_verdict
    if base.actual_verdict != "pass":
        failures.append(
            Failure(
                "axis_mutate",
                f"{op}: conforming baseline must pass before mutate "
                f"(got {base.actual_verdict}: {base.reason})",
            )
        )
        return failures, evidence

    apply = mutator or (lambda d: mutate_axis(d, op))
    try:
        mutated = apply(conf)
    except Exception as e:  # noqa: BLE001
        failures.append(Failure("axis_mutate", f"{op}: mutator raised {e}"))
        return failures, evidence

    fp_before = axis_fingerprint(conf)
    fp_after = axis_fingerprint(mutated)
    evidence["axis_changed"] = fp_before != fp_after
    if fp_before == fp_after:
        failures.append(
            Failure(
                "axis_mutate",
                f"{op}: no axis payload change "
                f"(verdict-only / cosmetic edits do not count as power)",
            )
        )
        return failures, evidence

    claimed = (mutated.get("meta") or {}).get("claimed_axis")
    try:
        witness = build_semantic_witness(op, conf, mutated, claimed_axis=claimed)
    except Exception as e:  # noqa: BLE001
        failures.append(Failure("axis_mutate", f"{op}: semantic witness error: {e}"))
        return failures, evidence

    evidence["semantic_witness"] = witness.to_dict()
    evidence["expected_axis"] = witness.expected_axis
    evidence["channel"] = witness.channel

    if not witness.axis_match:
        failures.append(
            Failure(
                "axis_mutate",
                f"{op}: wrong-axis mutate (claimed={witness.details.get('claimed_axis')!r} "
                f"expected={witness.expected_axis!r}); semantic witness rejected",
            )
        )
        return failures, evidence

    if not witness.changed:
        failures.append(
            Failure(
                "axis_mutate",
                f"{op}: semantic witness before==after on axis {witness.expected_axis!r} "
                f"(channel={witness.channel})",
            )
        )
        return failures, evidence

    try:
        if op in CRN_REQUIRED and mutated.get("coupling") != "crn_closed_loop":
            failures.append(
                Failure("axis_mutate", f"{op}: mutator dropped crn_closed_loop coupling")
            )
            return failures, evidence
        mutated.setdefault("control_role", conf.get("control_role", "conforming"))
        mutated.setdefault("expected_verdict", conf.get("expected_verdict", "pass"))
        validate_envelope(mutated)
        after = grade_document(mutated)
    except ContractError as e:
        failures.append(Failure("axis_mutate", f"{op}: mutated grade error: {e}"))
        return failures, evidence

    evidence["mutated_verdict"] = after.actual_verdict
    evidence["baseline_reason"] = base.reason
    evidence["mutated_reason"] = after.reason
    if after.actual_verdict != "fail":
        failures.append(
            Failure(
                "axis_mutate",
                f"{op}: axis mutate did not flip pass→fail "
                f"(baseline={base.actual_verdict}, mutated={after.actual_verdict}: {after.reason})",
            )
        )
        return failures, evidence

    # Probe-tree branch mirror (same mutator path) for structured execution evidence.
    try:
        branch = execute_mutate_axis_branch(op, conf, mutator=apply, claimed_axis=claimed)
        evidence["probe_tree_branch"] = {
            "branch": branch.get("branch"),
            "power_ok": branch.get("power_ok"),
            "expected_axis": branch.get("expected_axis"),
            "channel": branch.get("channel"),
        }
        # Structural prefix-share counters (protocol fields; not $ / timing).
        horizon = conf.get("horizon") if isinstance(conf.get("horizon"), dict) else {}
        try:
            hlen = int(horizon.get("length", 0) or 0)
        except (TypeError, ValueError):
            hlen = 0
        from diptych.probe_tree import prefix_share_amortization

        evidence["amortization"] = prefix_share_amortization(hlen)
    except Exception:  # noqa: BLE001
        evidence["probe_tree_branch"] = None

    evidence["power_ok"] = True
    return failures, evidence


def run_gates() -> Report:
    failures = gate_manifest()
    if failures:
        return Report(ok=False, failures=failures)

    paths = [_path(op, role) for op in OPERATORS for role in ("conforming", "violating")]
    failures.extend(gate_stubs(paths))

    results: list[dict[str, Any]] = []
    matrix_ops: dict[str, Any] = {}
    axis_power: dict[str, Any] = {}

    for op in OPERATORS:
        conf = load_probe(_path(op, "conforming"))
        viol = load_probe(_path(op, "violating"))
        failures.extend(gate_contrast(op, conf, viol))
        failures.extend(gate_axis(op, conf, viol))

        # Power-on-axis AFTER contrast / axis-presence checks
        mutate_failures, power_ev = gate_axis_mutate(op, conf)
        failures.extend(mutate_failures)
        axis_power[op] = power_ev
        mutate_ok = power_ev.get("power_ok") is True

        grades: list[GradeResult] = []
        grade_error = False
        for doc in (conf, viol):
            try:
                validate_envelope(doc)
                g = grade_document(doc)
            except ContractError as e:
                failures.append(Failure("grade", f"{op}: {e}"))
                matrix_ops[op] = {
                    "diptych_core": "stub",
                    "zeroday": _adapter_cell("zeroday"),
                    "aomb": _adapter_cell("aomb"),
                    "axis_power": False,
                }
                grade_error = True
                break
            grades.append(g)
            results.append(g.to_dict())
            if not g.matches_expected:
                failures.append(
                    Failure(
                        "contrast",
                        f"{op}/{g.control_role}: expected {g.expected_verdict} "
                        f"got {g.actual_verdict} ({g.reason})",
                    )
                )
        if grade_error:
            continue

        twin_ok = (
            len(grades) == 2
            and all(g.matches_expected for g in grades)
            and grades[0].actual_verdict == "pass"
            and grades[1].actual_verdict == "fail"
        )
        # diptych_core green ONLY if twin contrast AND mutate-axis power both pass
        green = twin_ok and mutate_ok
        matrix_ops[op] = {
            "diptych_core": "green" if green else "pending",
            "zeroday": _adapter_cell("zeroday"),
            "aomb": _adapter_cell("aomb"),
            "axis_power": mutate_ok,
        }

    matrix = {
        "diptych_schema": SCHEMA,
        "source_row": SOURCE,
        "operators": matrix_ops,
        "notes": (
            "diptych_core=green requires twin conf/viol AND gate_axis_mutate power-on-axis; "
            "zeroday=green and aomb=green at pins ZeroDay@fb5b39daf88e37521aaee8526ae9d286cf74f341 "
            "(merged #41+#42) and AOMB@667e47538ae5b9c504187b7a73220d22aa8fb96f (merged #18); "
            "no AUROC / invented model scores"
        ),
    }
    ok = not failures and all(v.get("diptych_core") == "green" for v in matrix_ops.values())
    if not ok and not failures:
        failures.append(Failure("matrix", "not all diptych_core cells green"))
    return Report(
        ok=ok,
        failures=failures,
        results=results,
        matrix=matrix,
        axis_power=axis_power,
    )


def write_matrix(matrix: dict[str, Any], path: Path = MATRIX) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(matrix, indent=2) + "\n", encoding="utf-8")
