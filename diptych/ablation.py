"""RQ2 protocol harness: operator leave-one-out ablation on cassette controls.

Protocol scaffolding on the **handwritten cassette control bank only**
(information / redundancy scaffolding). No AUROC / no model scores.

1. Full-8 hyperproperty grade: every operator separates conforming vs
   violating (reuses ``diptych.separation.evaluate_operator_separation`` /
   ``grade_operator``).
2. For each held-out operator O: grade with the other 7 only; record whether
   the remaining bank still separates for every peer, and whether ablating O
   loses the separation full-8 had for O's own contrast.
3. Per-op structural flags::

    marginal_necessary     — ablating O drops separation for O's own contrast
    redundancy_with_peers  — remaining 7 still all separate (peers do not
                             need O for their own contrasts)

Aggregate JSON carries explicit non-claims: protocol scaffolding on controls,
NOT an empirical RQ2 answer / NOT AUROC.

Exit non-zero if the full-8 control bank fails to separate
(``full8_ok`` false / ``control_separation_index < 1.0``).

Stranger path::

    python -m diptych.ablation
    python -m diptych.ablation --json
    diptych-ablation
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable

from diptych import OPERATORS
from diptych.coupling import CANONICAL_COUPLING
from diptych.separation import (
    DEFAULT_CASSETTE,
    DEFAULT_TWIN_INDEX,
    evaluate_operator_separation,
)

EXIT_OK = 0
EXIT_FAIL = 1


def _hyper_cell(
    op: str,
    *,
    cassette: Path | None,
    twin_index: int,
    single_trace_grader: Callable[..., dict[str, Any]] | None,
    hyper_grader: Callable[[dict[str, Any]], Any] | None,
) -> dict[str, Any]:
    """One operator separation cell; tolerate missing probes as error rows."""
    try:
        cell = evaluate_operator_separation(
            op,
            cassette=cassette,
            twin_index=twin_index,
            single_trace_grader=single_trace_grader,
            hyper_grader=hyper_grader,
        )
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        cell = {
            "operator": op.upper(),
            "single_trace_equiv": False,
            "hyper_separates": False,
            "separates": False,
            "error": str(exc),
        }
    return cell


def build_full8_bank(
    *,
    cassette: Path | None = None,
    twin_index: int = DEFAULT_TWIN_INDEX,
    single_trace_grader: Callable[..., dict[str, Any]] | None = None,
    hyper_grader: Callable[[dict[str, Any]], Any] | None = None,
) -> dict[str, Any]:
    """Full-8 hyperproperty separation bank (reuse separation machinery)."""
    cassette_path = Path(cassette) if cassette is not None else DEFAULT_CASSETTE
    operators: dict[str, Any] = {}
    failures: list[str] = []
    separates_count = 0
    hyper_separates_count = 0

    for op in OPERATORS:
        cell = _hyper_cell(
            op,
            cassette=cassette_path,
            twin_index=twin_index,
            single_trace_grader=single_trace_grader,
            hyper_grader=hyper_grader,
        )
        operators[op] = cell
        if cell.get("separates"):
            separates_count += 1
        if cell.get("hyper_separates"):
            hyper_separates_count += 1
        if not cell.get("hyper_separates"):
            failures.append(f"{op}:hyper_separates")
        if cell.get("error"):
            failures.append(f"{op}:error")

    n_ops = len(OPERATORS)
    control_separation_index = (separates_count / n_ops) if n_ops else 0.0
    if control_separation_index < 1.0:
        failures.append("control_separation_index<1.0")
    if hyper_separates_count < n_ops:
        failures.append("full8_hyper_separates<8")

    ok = not failures and hyper_separates_count == n_ops and control_separation_index >= 1.0
    return {
        "ok": ok,
        "operator_count": n_ops,
        "separates_count": separates_count,
        "hyper_separates_count": hyper_separates_count,
        "control_separation_index": control_separation_index,
        "operators": operators,
        "failures": failures,
    }


def evaluate_leave_one_out(
    held_out: str,
    full8: dict[str, Any],
    *,
    cassette: Path | None = None,
    twin_index: int = DEFAULT_TWIN_INDEX,
    single_trace_grader: Callable[..., dict[str, Any]] | None = None,
    hyper_grader: Callable[[dict[str, Any]], Any] | None = None,
) -> dict[str, Any]:
    """Ablate one operator; grade remaining 7; structural necessity/redundancy."""
    op_u = held_out.upper()
    if op_u not in OPERATORS:
        raise KeyError(f"unknown operator {held_out!r}")

    cassette_path = Path(cassette) if cassette is not None else DEFAULT_CASSETTE
    remaining = [op for op in OPERATORS if op != op_u]
    remaining_ops: dict[str, Any] = {}
    remaining_separates = 0
    remaining_hyper = 0

    for op in remaining:
        # Prefer already-graded full-8 cells (same cassette/graders) for stability.
        prior = (full8.get("operators") or {}).get(op)
        if (
            prior is not None
            and single_trace_grader is None
            and hyper_grader is None
            and "hyper_separates" in prior
        ):
            cell = prior
        else:
            cell = _hyper_cell(
                op,
                cassette=cassette_path,
                twin_index=twin_index,
                single_trace_grader=single_trace_grader,
                hyper_grader=hyper_grader,
            )
        remaining_ops[op] = {
            "hyper_separates": bool(cell.get("hyper_separates")),
            "separates": bool(cell.get("separates")),
            "error": cell.get("error"),
        }
        if cell.get("separates"):
            remaining_separates += 1
        if cell.get("hyper_separates"):
            remaining_hyper += 1

    remaining_bank_separates = remaining_hyper == len(remaining) and all(
        not (remaining_ops[op].get("error")) for op in remaining
    )

    full8_cell = (full8.get("operators") or {}).get(op_u) or {}
    full8_own_hyper = bool(full8_cell.get("hyper_separates"))
    # Ablating O removes O from the graded bank → loses O's own contrast
    # separation that full-8 had (structural; not a model score).
    loses_own_separation = full8_own_hyper
    marginal_necessary = loses_own_separation
    # Peers still separate their own contrasts without O.
    redundancy_with_peers = remaining_bank_separates

    coupling = CANONICAL_COUPLING.get(op_u)
    return {
        "held_out": op_u,
        "coupling": coupling,
        "remaining_operator_count": len(remaining),
        "remaining_hyper_separates_count": remaining_hyper,
        "remaining_separates_count": remaining_separates,
        "remaining_bank_separates": remaining_bank_separates,
        "full8_own_hyper_separates": full8_own_hyper,
        "loses_own_separation": loses_own_separation,
        "marginal_necessary": marginal_necessary,
        "redundancy_with_peers": redundancy_with_peers,
        "remaining_operators": remaining_ops,
    }


def _coupling_strata_summary(
    full8: dict[str, Any],
    ablations: dict[str, Any],
) -> dict[str, Any]:
    """Structural open_loop vs crn_closed_loop rollup (not AUROC)."""
    strata: dict[str, Any] = {}
    for coupling in ("open_loop", "crn_closed_loop"):
        ops = [op for op in OPERATORS if CANONICAL_COUPLING.get(op) == coupling]
        hyper_ok = 0
        for op in ops:
            cell = (full8.get("operators") or {}).get(op) or {}
            if cell.get("hyper_separates"):
                hyper_ok += 1
        marginal = sum(
            1
            for op in ops
            if (ablations.get(op) or {}).get("marginal_necessary")
        )
        redundant = sum(
            1
            for op in ops
            if (ablations.get(op) or {}).get("redundancy_with_peers")
        )
        strata[coupling] = {
            "operators": ops,
            "operator_count": len(ops),
            "full8_hyper_separates_count": hyper_ok,
            "marginal_necessary_count": marginal,
            "redundancy_with_peers_count": redundant,
        }
    return strata


def build_ablation_report(
    *,
    cassette: Path | None = None,
    twin_index: int = DEFAULT_TWIN_INDEX,
    single_trace_grader: Callable[..., dict[str, Any]] | None = None,
    hyper_grader: Callable[[dict[str, Any]], Any] | None = None,
) -> dict[str, Any]:
    """Full leave-one-out ablation report (protocol scaffolding only)."""
    cassette_path = Path(cassette) if cassette is not None else DEFAULT_CASSETTE
    full8 = build_full8_bank(
        cassette=cassette_path,
        twin_index=twin_index,
        single_trace_grader=single_trace_grader,
        hyper_grader=hyper_grader,
    )

    ablations: dict[str, Any] = {}
    for op in OPERATORS:
        ablations[op] = evaluate_leave_one_out(
            op,
            full8,
            cassette=cassette_path,
            twin_index=twin_index,
            single_trace_grader=single_trace_grader,
            hyper_grader=hyper_grader,
        )

    strata = _coupling_strata_summary(full8, ablations)
    failures = list(full8.get("failures") or [])
    # Protocol gate: full-8 must separate; LOO flags are informational.
    ok = bool(full8.get("ok")) and not failures

    return {
        "ok": ok,
        "operator_count": len(OPERATORS),
        "cassette": str(cassette_path.resolve()),
        "twin_index": twin_index,
        "full8": {
            "ok": full8.get("ok"),
            "operator_count": full8.get("operator_count"),
            "separates_count": full8.get("separates_count"),
            "hyper_separates_count": full8.get("hyper_separates_count"),
            "control_separation_index": full8.get("control_separation_index"),
            "operators": {
                op: {
                    "single_trace_equiv": cell.get("single_trace_equiv"),
                    "hyper_separates": cell.get("hyper_separates"),
                    "separates": cell.get("separates"),
                    "error": cell.get("error"),
                }
                for op, cell in (full8.get("operators") or {}).items()
            },
            "failures": list(full8.get("failures") or []),
        },
        "ablations": ablations,
        "coupling_strata": strata,
        "failures": failures,
        "notes": (
            "RQ2 protocol harness — operator leave-one-out ablation on "
            "handwritten cassette controls only (information / redundancy "
            "scaffolding). full8 = hyperproperty separation bank via "
            "diptych.separation / grade_operator; for each held-out O, the "
            "other 7 are graded and remaining_bank_separates / "
            "loses_own_separation are recorded. marginal_necessary = ablating "
            "O drops separation for O's own contrast; redundancy_with_peers = "
            "remaining bank still separates for every peer (structural flags "
            "only). NOT AUROC / NOT model scores / NOT an empirical RQ2 answer."
        ),
        "non_claims": [
            "NOT AUROC",
            "NOT model scores / ranks",
            "NOT an empirical RQ2 answer",
            "protocol scaffolding on cassette controls only",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    """CLI: ``python -m diptych.ablation`` / ``diptych-ablation``."""
    p = argparse.ArgumentParser(
        prog="diptych-ablation",
        description=(
            "RQ2 protocol harness: operator leave-one-out ablation on "
            "handwritten cassette controls. Reports per-op "
            "marginal_necessary / redundancy_with_peers (structural flags "
            "only — not AUROC / model score / empirical RQ2 answer). "
            "Exit non-zero if the full-8 control bank fails to separate."
        ),
    )
    p.add_argument(
        "--cassette",
        type=Path,
        default=DEFAULT_CASSETTE,
        help=(
            "Cassette root with {OP}/{conforming,violating}/probe.json "
            f"(default: {DEFAULT_CASSETTE})"
        ),
    )
    p.add_argument(
        "--twin-index",
        type=int,
        default=DEFAULT_TWIN_INDEX,
        help=(
            "Twin index forwarded to separation baseline "
            f"(default: {DEFAULT_TWIN_INDEX})"
        ),
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON report on stdout",
    )
    args = p.parse_args(list(argv) if argv is not None else None)

    report = build_ablation_report(
        cassette=args.cassette,
        twin_index=args.twin_index,
    )
    if args.json:
        sys.stdout.write(json.dumps(report, indent=2) + "\n")
    else:
        print("== DIPTYCH ablation (RQ2 protocol harness) ==")
        print(f"cassette={report['cassette']}")
        full8 = report["full8"]
        print(
            f"full8: ok={full8['ok']} "
            f"hyper_separates_count={full8['hyper_separates_count']} "
            f"control_separation_index={full8['control_separation_index']}"
        )
        print(
            "(protocol scaffolding on cassette controls — NOT AUROC / "
            "model score / empirical RQ2 answer)"
        )
        for op, cell in report["ablations"].items():
            status = "OK  " if cell.get("remaining_bank_separates") else "FAIL"
            print(
                f"  {status} hold-out={op:12} "
                f"marginal_necessary={cell.get('marginal_necessary')} "
                f"redundancy_with_peers={cell.get('redundancy_with_peers')} "
                f"remaining_bank_separates={cell.get('remaining_bank_separates')}"
            )
        for coupling, stratum in report["coupling_strata"].items():
            print(
                f"  stratum {coupling}: "
                f"ops={stratum['operator_count']} "
                f"full8_hyper={stratum['full8_hyper_separates_count']} "
                f"marginal={stratum['marginal_necessary_count']} "
                f"redundant={stratum['redundancy_with_peers_count']}"
            )
        if report["ok"]:
            print("ABLATION OK")
        else:
            print(f"ABLATION FAIL ({', '.join(report['failures'])})")
    return EXIT_OK if report["ok"] else EXIT_FAIL


if __name__ == "__main__":
    raise SystemExit(main())
