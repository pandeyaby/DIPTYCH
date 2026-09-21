"""Unified RQ1–RQ5 protocol report CLI.

One stranger command runs the existing RQ harnesses and emits a single JSON
aggregate. Reuses report builders — does not reimplement grading logic.

    RQ1 separation   → diptych.separation.build_separation_report
    RQ2 ablation     → diptych.ablation.build_ablation_report
    RQ3 predictive   → diptych.predictive.build_predictive_report
    RQ4 amortization → diptych.amortization.build_amortization_report
    RQ5 inconclusive → diptych.inconclusive.build_inconclusive_report

Overall ``ok`` iff every subreport is ok. RQ3 ``protocol_only`` / N/A without
held-out is ok (never invents AUROC / model ranks / correlations).

Stranger path::

    python -m diptych.protocol
    python -m diptych.protocol --json
    diptych-protocol
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from diptych import SCHEMA
from diptych.ablation import build_ablation_report
from diptych.amortization import build_amortization_report
from diptych.inconclusive import build_inconclusive_report
from diptych.predictive import NA, build_predictive_report
from diptych.separation import build_separation_report

PROTOCOL_SCHEMA = "1.0"

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASSETTE = ROOT / "examples" / "fixtures" / "cassette"
DEFAULT_JSON_OUT = ROOT / "reports" / "paired-probes" / "protocol_report.json"

EXIT_OK = 0
EXIT_FAIL = 1

RQ_IDS: tuple[str, ...] = ("RQ1", "RQ2", "RQ3", "RQ4", "RQ5")

NON_CLAIMS: list[str] = [
    "NOT AUROC",
    "NOT model ranks / scores",
    "NOT invented correlations / matrix greens / dollar costs",
    "NOT empirical RQ1–RQ5 answers",
    "protocol scaffolding only — structural harness metrics or explicit N/A",
]

NOTES = (
    "Unified RQ1–RQ5 protocol report. Invokes existing harness builders "
    "(separation / ablation / predictive / amortization / inconclusive) and "
    "aggregates per-RQ ok/status/key metrics. overall ok iff all subreports "
    "ok. RQ3 without --held-out stays protocol_only with N/A result cells. "
    "Never invents AUROC / model ranks / $ / matrix greens / empirical RQ answers."
)


def _rq_cell(
    *,
    rq: str,
    harness: str,
    ok: bool,
    status: str,
    metrics: dict[str, Any],
    failures: list[str] | None = None,
    non_claims: list[str] | None = None,
) -> dict[str, Any]:
    cell: dict[str, Any] = {
        "rq": rq,
        "harness": harness,
        "ok": bool(ok),
        "status": status,
        "metrics": metrics,
        "failures": list(failures or []),
    }
    if non_claims is not None:
        cell["non_claims"] = list(non_claims)
    return cell


def _summarize_rq1(report: dict[str, Any]) -> dict[str, Any]:
    ok = bool(report.get("ok"))
    return _rq_cell(
        rq="RQ1",
        harness="separation",
        ok=ok,
        status="ok" if ok else "fail",
        metrics={
            "operator_count": report.get("operator_count"),
            "separates_count": report.get("separates_count"),
            "control_separation_index": report.get("control_separation_index"),
        },
        failures=list(report.get("failures") or []),
    )


def _summarize_rq2(report: dict[str, Any]) -> dict[str, Any]:
    ok = bool(report.get("ok"))
    full8 = report.get("full8") or {}
    ablations = report.get("ablations") or {}
    n_marginal = sum(
        1 for cell in ablations.values() if cell.get("marginal_necessary")
    )
    n_redundant = sum(
        1 for cell in ablations.values() if cell.get("redundancy_with_peers")
    )
    return _rq_cell(
        rq="RQ2",
        harness="ablation",
        ok=ok,
        status="ok" if ok else "fail",
        metrics={
            "operator_count": report.get("operator_count"),
            "full8_ok": full8.get("ok"),
            "full8_hyper_separates_count": full8.get("hyper_separates_count"),
            "control_separation_index": full8.get("control_separation_index"),
            "ops_marginal_necessary": n_marginal,
            "ops_redundancy_with_peers": n_redundant,
        },
        failures=list(report.get("failures") or []),
        non_claims=list(report.get("non_claims") or []),
    )


def _summarize_rq3(report: dict[str, Any]) -> dict[str, Any]:
    """RQ3: protocol_only / N/A without held-out is ok; never invent scores."""
    results = report.get("results") or {}
    status = str(report.get("status") or "unknown")
    ok = bool(report.get("ok"))
    # Explicit N/A cells when protocol_only; pass through structural values only.
    metrics: dict[str, Any] = {
        "held_out": report.get("held_out"),
        "n_pairs": report.get("n_pairs"),
        "probe_held_out_rank_corr": results.get("probe_held_out_rank_corr", NA),
        "trace_only_rank_corr": results.get("trace_only_rank_corr", NA),
        "probe_beats_trace_baseline": results.get(
            "probe_beats_trace_baseline", NA
        ),
        "n_pairs_usable": results.get("n_pairs_usable", 0),
    }
    return _rq_cell(
        rq="RQ3",
        harness="predictive",
        ok=ok,
        status=status,
        metrics=metrics,
        failures=list(report.get("failures") or []),
        non_claims=list(report.get("non_claims") or []),
    )


def _summarize_rq4(report: dict[str, Any]) -> dict[str, Any]:
    ok = bool(report.get("ok"))
    agg = report.get("aggregate") or {}
    am = agg.get("amortization") or {}
    return _rq_cell(
        rq="RQ4",
        harness="amortization",
        ok=ok,
        status="ok" if ok else "fail",
        metrics={
            "operator_count": report.get("operator_count"),
            "alpha": agg.get("alpha"),
            "counters_consistent": agg.get("counters_consistent"),
            "nodes_naive_two_probes": am.get("nodes_naive_two_probes"),
            "nodes_with_prefix_share": am.get("nodes_with_prefix_share"),
            "nodes_saved_by_prefix_share": am.get("nodes_saved_by_prefix_share"),
        },
        failures=list(report.get("failures") or []),
        non_claims=list(report.get("non_claims") or []),
    )


def _summarize_rq5(report: dict[str, Any]) -> dict[str, Any]:
    ok = bool(report.get("ok"))
    return _rq_cell(
        rq="RQ5",
        harness="inconclusive",
        ok=ok,
        status="ok" if ok else "fail",
        metrics={
            "operator_count": report.get("operator_count"),
            "ops_with_working_inconclusive": report.get(
                "ops_with_working_inconclusive"
            ),
            "n_probes": report.get("n_probes"),
            "n_inconclusive": report.get("n_inconclusive"),
            "inconclusive_rate": report.get("inconclusive_rate"),
            "by_reason_needle": report.get("by_reason_needle") or {},
        },
        failures=list(report.get("failures") or []),
        non_claims=list(report.get("non_claims") or []),
    )


def build_protocol_report(
    *,
    cassette: Path | None = None,
    held_out: Path | None = None,
) -> dict[str, Any]:
    """Aggregate RQ1–RQ5 harness reports into one protocol JSON."""
    cassette_path = Path(cassette) if cassette is not None else DEFAULT_CASSETTE

    sep = build_separation_report(cassette=cassette_path)
    abl = build_ablation_report(cassette=cassette_path)
    pred = build_predictive_report(held_out=held_out)
    amort = build_amortization_report(cassette=cassette_path)
    inc = build_inconclusive_report(cassette=cassette_path)

    rqs: dict[str, Any] = {
        "RQ1": _summarize_rq1(sep),
        "RQ2": _summarize_rq2(abl),
        "RQ3": _summarize_rq3(pred),
        "RQ4": _summarize_rq4(amort),
        "RQ5": _summarize_rq5(inc),
    }

    failures = [rq for rq in RQ_IDS if not rqs[rq].get("ok")]
    # Loud non-claims: top-level + unique from subreports.
    non_claims: list[str] = list(NON_CLAIMS)
    seen = {c.lower() for c in non_claims}
    for rq in RQ_IDS:
        for claim in rqs[rq].get("non_claims") or []:
            key = str(claim).lower()
            if key not in seen:
                non_claims.append(str(claim))
                seen.add(key)

    return {
        "protocol_schema": PROTOCOL_SCHEMA,
        "diptych_schema": SCHEMA,
        "ok": not failures,
        "rq_count": len(RQ_IDS),
        "rqs": rqs,
        "failures": failures,
        "cassette": str(cassette_path.resolve()),
        "held_out": str(Path(held_out).resolve()) if held_out is not None else None,
        "notes": NOTES,
        "non_claims": non_claims,
    }


def _print_human(report: dict[str, Any]) -> None:
    print("== DIPTYCH protocol (RQ1–RQ5) ==")
    print(
        f"diptych_schema={report.get('diptych_schema')} "
        f"protocol_schema={report.get('protocol_schema')}"
    )
    for rq_id in RQ_IDS:
        cell = (report.get("rqs") or {}).get(rq_id) or {}
        status = "OK  " if cell.get("ok") else "FAIL"
        metrics = cell.get("metrics") or {}
        bits: list[str] = [f"status={cell.get('status')}"]
        for key in (
            "control_separation_index",
            "separates_count",
            "full8_hyper_separates_count",
            "ops_marginal_necessary",
            "probe_held_out_rank_corr",
            "trace_only_rank_corr",
            "alpha",
            "n_inconclusive",
            "inconclusive_rate",
            "ops_with_working_inconclusive",
        ):
            if key in metrics:
                bits.append(f"{key}={metrics[key]}")
        line = f"  {status} {rq_id} ({cell.get('harness')}) — " + ", ".join(bits)
        print(line)
    print(f"non_claims={report.get('non_claims')}")
    if report.get("ok"):
        print("PROTOCOL OK")
    else:
        print(f"PROTOCOL FAIL ({', '.join(report.get('failures') or [])})")


def _cli_prog() -> str:
    name = Path(sys.argv[0]).name
    if name in ("diptych-protocol", "diptych-protocol.exe"):
        return "diptych-protocol"
    return "python -m diptych.protocol"


def main(argv: list[str] | None = None) -> int:
    """CLI: ``python -m diptych.protocol`` / ``diptych-protocol``."""
    p = argparse.ArgumentParser(
        prog=_cli_prog(),
        description=(
            "Unified RQ1–RQ5 protocol report: runs separation, ablation, "
            "predictive, amortization, and inconclusive harnesses; emits one "
            "JSON aggregate. RQ3 without --held-out stays protocol_only/N/A "
            "(ok). Exit non-zero if any RQ harness fails. Never invents "
            "AUROC / model ranks / $ / matrix greens."
        ),
    )
    p.add_argument(
        "--cassette",
        type=Path,
        default=DEFAULT_CASSETTE,
        help=f"Cassette fixture root (default: {DEFAULT_CASSETTE})",
    )
    p.add_argument(
        "--held-out",
        type=Path,
        default=None,
        help=(
            "Optional RQ3 held-out outcomes JSON (forwarded to predictive). "
            "Missing/malformed/empty → fail closed."
        ),
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable protocol report JSON",
    )
    p.add_argument(
        "--out",
        type=Path,
        default=None,
        help=f"Write protocol JSON to path (default: {DEFAULT_JSON_OUT} when --json)",
    )
    p.add_argument(
        "--stdout-only",
        action="store_true",
        help="With --json: print JSON to stdout only (no file write)",
    )
    p.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress human table (JSON still emitted when requested)",
    )
    args = p.parse_args(list(argv) if argv is not None else None)

    report = build_protocol_report(cassette=args.cassette, held_out=args.held_out)
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

    return EXIT_OK if report.get("ok") else EXIT_FAIL


if __name__ == "__main__":
    raise SystemExit(main())
