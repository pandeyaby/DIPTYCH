"""RQ3 protocol harness: predictive validity vs held-out robustness.

Protocol scaffolding only. **No AUROC / no model ranks / no invented
correlations / no empirical RQ3 answer.**

Default mode (no ``--held-out`` file)::

    result cells stay explicitly ``N/A``
    status = ``protocol_only``
    ok = true

Optional ``--held-out PATH`` — documented JSON schema (see
``HELD_OUT_SCHEMA`` / ``load_held_out``). Missing, malformed, or empty
files exit non-zero (``ok=false``). When a valid non-empty file is
present, this module may compute **structural** Spearman rank-correlation
placeholders from the supplied numeric pairs only; if pairs are
insufficient (too few, non-finite, or zero variance after ranking),
result cells remain ``N/A`` — values are never invented.

Protocol (paper ``RQ_PROTOCOL.md`` RQ3)::

    probe scores ↔ held-out robustness outcomes (rank correlation)
    baseline = trace-only score ↔ same held-out outcomes

Stranger path::

    python -m diptych.predictive
    python -m diptych.predictive --json
    python -m diptych.predictive --held-out path/to/held_out.json --json
    diptych-predictive
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

EXIT_OK = 0
EXIT_FAIL = 1

# Documented held-out envelope version. Bump only with a real schema change.
HELD_OUT_SCHEMA_ID = "diptych.predictive.held_out"
HELD_OUT_SCHEMA_VERSION = "1.0"

NA = "N/A"

NON_CLAIMS: list[str] = [
    "NOT AUROC",
    "NOT model ranks / scores",
    "NOT an empirical RQ3 answer",
    "NOT invented correlations / matrix greens",
    "protocol scaffolding only until real held-out pairs exist",
]

NOTES = (
    "RQ3 protocol harness — predictive validity scaffolding "
    "(probe scores vs held-out robustness; baseline = trace-only). "
    "Without --held-out, all result cells are N/A and status=protocol_only. "
    "With --held-out, the file must match HELD_OUT_SCHEMA (non-empty pairs of "
    "numeric probe_score / trace_score / held_out_outcome); missing, "
    "malformed, or empty inputs fail closed. Rank-correlation placeholders "
    "are computed only from supplied pairs and stay N/A when inputs are "
    "insufficient — never invent AUROC / model ranks / fake correlations. "
    "NOT AUROC / NOT model ranks / NOT an empirical RQ3 answer."
)

HELD_OUT_SCHEMA: dict[str, Any] = {
    "schema": HELD_OUT_SCHEMA_ID,
    "schema_version": HELD_OUT_SCHEMA_VERSION,
    "pairs_key": "pairs",
    "pair_required_numeric": [
        "probe_score",
        "trace_score",
        "held_out_outcome",
    ],
    "pair_optional": ["id"],
    "example": {
        "schema": HELD_OUT_SCHEMA_ID,
        "schema_version": HELD_OUT_SCHEMA_VERSION,
        "pairs": [
            {
                "id": "scenario-a",
                "probe_score": 0.0,
                "trace_score": 0.0,
                "held_out_outcome": 0.0,
            }
        ],
    },
    "notes": (
        "Top-level object with schema/schema_version matching this document "
        "and a non-empty pairs list. Each pair must carry finite numeric "
        "probe_score, trace_score, and held_out_outcome. Empty pairs / "
        "missing file / bad JSON → harness fails closed."
    ),
}

PROTOCOL: dict[str, Any] = {
    "rq": "RQ3",
    "question": (
        "Do hyperproperty / probe scores predict held-out robustness "
        "better than trace-only scores?"
    ),
    "metric": (
        "Rank correlation (probe score ↔ held-out outcome); "
        "baseline = trace-only correlation with the same outcomes"
    ),
    "harness_answer": (
        "Hold out adversarial scenarios from probe construction; "
        "score on training scenarios only; compare probe vs trace-only "
        "rank correlation against held-out outcomes"
    ),
    "baseline": "trace-only",
    "result_until_held_out": NA,
    "held_out_schema": HELD_OUT_SCHEMA,
}


def _na_results() -> dict[str, Any]:
    """Explicit N/A result cells — never invent correlations."""
    return {
        "probe_held_out_rank_corr": NA,
        "trace_only_rank_corr": NA,
        "probe_beats_trace_baseline": NA,
        "n_pairs_usable": 0,
    }


def _average_ranks(values: list[float]) -> list[float]:
    """1-based average ranks (ties share the mean rank)."""
    n = len(values)
    order = sorted(range(n), key=lambda i: values[i])
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + 1 + j + 1) / 2.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    """Pearson r, or None when undefined (n<2 or zero variance)."""
    n = len(xs)
    if n < 2 or n != len(ys):
        return None
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    num = 0.0
    den_x = 0.0
    den_y = 0.0
    for x, y in zip(xs, ys):
        dx = x - mean_x
        dy = y - mean_y
        num += dx * dy
        den_x += dx * dx
        den_y += dy * dy
    if den_x <= 0.0 or den_y <= 0.0:
        return None
    return num / math.sqrt(den_x * den_y)


def spearman_rank_corr(xs: list[float], ys: list[float]) -> float | None:
    """Structural Spearman ρ on supplied pairs; None if insufficient."""
    if len(xs) < 2 or len(xs) != len(ys):
        return None
    if not all(math.isfinite(v) for v in xs + ys):
        return None
    return _pearson(_average_ranks(xs), _average_ranks(ys))


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    f = float(value)
    if not math.isfinite(f):
        return None
    return f


def load_held_out(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    """Load + validate held-out JSON.

    Returns ``(pairs, failures)``. On any schema problem ``pairs`` is empty
    and ``failures`` is non-empty (caller must set ``ok=false``).
    """
    failures: list[str] = []
    if not path.exists():
        return [], [f"held_out_missing:{path}"]
    if not path.is_file():
        return [], [f"held_out_not_file:{path}"]

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [], [f"held_out_unreadable:{exc}"]

    if not isinstance(raw, dict):
        return [], ["held_out_not_object"]

    schema = raw.get("schema")
    version = raw.get("schema_version")
    if schema != HELD_OUT_SCHEMA_ID:
        failures.append(
            f"held_out_schema_id:{schema!r}!={HELD_OUT_SCHEMA_ID!r}"
        )
    if version != HELD_OUT_SCHEMA_VERSION:
        failures.append(
            f"held_out_schema_version:{version!r}!={HELD_OUT_SCHEMA_VERSION!r}"
        )

    pairs_raw = raw.get("pairs")
    if not isinstance(pairs_raw, list):
        failures.append("held_out_pairs_not_list")
        return [], failures
    if len(pairs_raw) == 0:
        failures.append("held_out_pairs_empty")
        return [], failures

    pairs: list[dict[str, Any]] = []
    for idx, item in enumerate(pairs_raw):
        if not isinstance(item, dict):
            failures.append(f"held_out_pair[{idx}]_not_object")
            continue
        probe = _finite_number(item.get("probe_score"))
        trace = _finite_number(item.get("trace_score"))
        outcome = _finite_number(item.get("held_out_outcome"))
        if probe is None or trace is None or outcome is None:
            failures.append(f"held_out_pair[{idx}]_non_finite_or_missing_numeric")
            continue
        pair: dict[str, Any] = {
            "probe_score": probe,
            "trace_score": trace,
            "held_out_outcome": outcome,
        }
        if "id" in item:
            pair["id"] = item.get("id")
        pairs.append(pair)

    if not pairs:
        failures.append("held_out_no_usable_pairs")
    # Schema failures (bad id/version) still fail closed even if pairs parse.
    return pairs, failures


def _correlation_results(pairs: list[dict[str, Any]]) -> dict[str, Any]:
    """Structural rank-corr placeholders; N/A when inputs are insufficient."""
    results = _na_results()
    if len(pairs) < 2:
        # Prefer N/A until real usable pairs exist (need ≥2 for rank corr).
        results["n_pairs_usable"] = len(pairs)
        return results

    probe = [float(p["probe_score"]) for p in pairs]
    trace = [float(p["trace_score"]) for p in pairs]
    outcome = [float(p["held_out_outcome"]) for p in pairs]

    probe_rho = spearman_rank_corr(probe, outcome)
    trace_rho = spearman_rank_corr(trace, outcome)

    results["n_pairs_usable"] = len(pairs)
    results["probe_held_out_rank_corr"] = (
        probe_rho if probe_rho is not None else NA
    )
    results["trace_only_rank_corr"] = (
        trace_rho if trace_rho is not None else NA
    )

    if probe_rho is None or trace_rho is None:
        results["probe_beats_trace_baseline"] = NA
    else:
        # Structural comparison of the two supplied correlations only.
        results["probe_beats_trace_baseline"] = bool(probe_rho > trace_rho)
    return results


def build_predictive_report(
    *,
    held_out: Path | None = None,
) -> dict[str, Any]:
    """RQ3 predictive-validity report (protocol scaffolding only)."""
    if held_out is None:
        return {
            "ok": True,
            "status": "protocol_only",
            "held_out": None,
            "n_pairs": 0,
            "results": _na_results(),
            "protocol": dict(PROTOCOL),
            "failures": [],
            "notes": NOTES,
            "non_claims": list(NON_CLAIMS),
        }

    held_path = Path(held_out)
    pairs, failures = load_held_out(held_path)
    if failures:
        return {
            "ok": False,
            "status": "held_out_invalid",
            "held_out": str(held_path),
            "n_pairs": 0,
            "results": _na_results(),
            "protocol": dict(PROTOCOL),
            "failures": failures,
            "notes": NOTES,
            "non_claims": list(NON_CLAIMS),
        }

    results = _correlation_results(pairs)
    return {
        "ok": True,
        "status": "held_out_loaded",
        "held_out": str(held_path.resolve()),
        "n_pairs": len(pairs),
        "results": results,
        "protocol": dict(PROTOCOL),
        "failures": [],
        "notes": NOTES,
        "non_claims": list(NON_CLAIMS),
    }


def main(argv: list[str] | None = None) -> int:
    """CLI: ``python -m diptych.predictive`` / ``diptych-predictive``."""
    p = argparse.ArgumentParser(
        prog="diptych-predictive",
        description=(
            "RQ3 protocol harness: predictive validity scaffolding "
            "(probe scores vs held-out robustness; baseline = trace-only). "
            "Without --held-out, emit N/A result cells (protocol_only). "
            "With --held-out, require the documented JSON schema; "
            "missing/malformed/empty → exit non-zero. Never invents AUROC / "
            "model ranks / fake correlations / empirical RQ3 answers."
        ),
    )
    p.add_argument(
        "--held-out",
        type=Path,
        default=None,
        help=(
            "Optional held-out outcomes JSON "
            f"(schema={HELD_OUT_SCHEMA_ID} v{HELD_OUT_SCHEMA_VERSION}). "
            "Missing/malformed/empty → fail closed."
        ),
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON report on stdout",
    )
    args = p.parse_args(list(argv) if argv is not None else None)

    report = build_predictive_report(held_out=args.held_out)
    if args.json:
        sys.stdout.write(json.dumps(report, indent=2) + "\n")
    else:
        print("== DIPTYCH predictive (RQ3 protocol harness) ==")
        print(f"status={report.get('status')} ok={report.get('ok')}")
        print(f"held_out={report.get('held_out')} n_pairs={report.get('n_pairs')}")
        results = report.get("results") or {}
        print(
            f"results: probe_held_out_rank_corr={results.get('probe_held_out_rank_corr')} "
            f"trace_only_rank_corr={results.get('trace_only_rank_corr')} "
            f"probe_beats_trace_baseline={results.get('probe_beats_trace_baseline')}"
        )
        print(
            "(probe scores vs held-out robustness; baseline = trace-only — "
            "NOT AUROC / model ranks / empirical RQ3 answer)"
        )
        print(f"non_claims={report.get('non_claims')}")
        if report.get("ok"):
            print("PREDICTIVE OK")
        else:
            print(f"PREDICTIVE FAIL ({', '.join(report.get('failures') or [])})")
    return EXIT_OK if report.get("ok") else EXIT_FAIL


if __name__ == "__main__":
    raise SystemExit(main())
