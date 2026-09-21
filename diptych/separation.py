"""RQ1 protocol harness: single-trace baseline vs hyperproperty separation.

Protocol scaffolding on the **handwritten cassette control bank only**.

For each of the 8 operators this module loads conforming + violating cassette
probes and reports:

* ``single_trace_equiv`` — baseline (trace-local) yields the same verdict on
  both roles
* ``hyper_separates`` — ``grade_operator`` yields conf=pass and viol=fail
* ``separates`` — both of the above

Aggregate::

    control_separation_index = separates_count / 8

This is a **structural fraction on the control bank**, not AUROC, not a model
score, and not an empirical RQ1 answer. Exit non-zero if any operator fails
``hyper_separates`` or ``control_separation_index < 1.0``.

Stranger path::

    python -m diptych.separation
    python -m diptych.separation --json
    diptych-separation
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable

from diptych import OPERATORS
from diptych.api import grade_operator

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASSETTE = ROOT / "examples" / "fixtures" / "cassette"

EXIT_OK = 0
EXIT_FAIL = 1

# Twin index inspected by the single-trace baseline (never the other twin).
DEFAULT_TWIN_INDEX = 0


def grade_single_trace_baseline(
    doc: dict[str, Any],
    *,
    twin_index: int = DEFAULT_TWIN_INDEX,
) -> dict[str, Any]:
    """Deliberately trace-local grader — cannot see hyperproperty contrast.

    **Rule (documented, intentional):**

    1. Select exactly one twin: ``traces[twin_index]`` (default: 0).
    2. Ignore every other twin. Do not compare twins. Do not read
       ``control_role`` / ``expected_verdict`` to decide the verdict.
    3. Emit ``pass`` iff that one twin is structurally well-formed
       (a dict with a non-empty ``channels`` mapping). Otherwise ``fail``.

    Why this cannot separate conforming vs violating on the cassette bank:
    hyperproperty operators (RESEED L∞ across seeds, SIGNFLIP odd symmetry,
    CRN residual coupling, schema keyset equality across twins, …) encode
    their contrast **across** the paired traces. A grader that only inspects
    one twin has no access to that contrast. On the handwritten cassette
    controls both roles carry a well-formed first twin by construction, so
    this baseline returns the **same** verdict on conf and viol
    (``single_trace_equiv``). Separation appears only under ``grade_operator``.

    This is a protocol baseline, not a competing product grader and not a
    model score.
    """
    traces = doc.get("traces")
    op = str(doc.get("operator") or "")
    probe_id = str(doc.get("probe_id") or "")
    control_role = str(doc.get("control_role") or "")
    expected = str(doc.get("expected_verdict") or "")

    if not isinstance(traces, list) or twin_index < 0 or twin_index >= len(traces):
        return {
            "operator": op,
            "probe_id": probe_id,
            "control_role": control_role,
            "expected_verdict": expected,
            "actual_verdict": "fail",
            "reason": f"single_trace_baseline: missing twin index {twin_index}",
            "evidence": {
                "baseline": "single_trace",
                "twin_index": twin_index,
                "twin_count": len(traces) if isinstance(traces, list) else 0,
            },
        }

    twin = traces[twin_index]
    if not isinstance(twin, dict):
        return {
            "operator": op,
            "probe_id": probe_id,
            "control_role": control_role,
            "expected_verdict": expected,
            "actual_verdict": "fail",
            "reason": "single_trace_baseline: twin is not an object",
            "evidence": {"baseline": "single_trace", "twin_index": twin_index},
        }

    channels = twin.get("channels")
    well_formed = isinstance(channels, dict) and len(channels) > 0
    # Intentionally ignore the other twin(s) and all cross-twin fields.
    return {
        "operator": op,
        "probe_id": probe_id,
        "control_role": control_role,
        "expected_verdict": expected,
        "actual_verdict": "pass" if well_formed else "fail",
        "reason": (
            "single_trace_baseline: well-formed twin "
            f"(channels={sorted(channels) if well_formed else []})"
            if well_formed
            else "single_trace_baseline: empty or missing channels on twin"
        ),
        "evidence": {
            "baseline": "single_trace",
            "twin_index": twin_index,
            "channel_names": sorted(channels) if well_formed else [],
            "twins_compared": False,
            "note": (
                "Inspects only one twin; cannot encode hyperproperty contrast."
            ),
        },
    }


def load_cassette_probe(
    op: str,
    role: str,
    cassette: Path | None = None,
) -> dict[str, Any]:
    """Load ``{cassette}/{OP}/{conforming|violating}/probe.json`` as a dict."""
    root = Path(cassette) if cassette is not None else DEFAULT_CASSETTE
    path = root / op.upper() / role / "probe.json"
    if not path.is_file():
        raise FileNotFoundError(f"cassette probe missing: {path}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"cassette probe is not an object: {path}")
    return raw


def evaluate_operator_separation(
    op: str,
    *,
    cassette: Path | None = None,
    twin_index: int = DEFAULT_TWIN_INDEX,
    single_trace_grader: Callable[..., dict[str, Any]] | None = None,
    hyper_grader: Callable[[dict[str, Any]], Any] | None = None,
) -> dict[str, Any]:
    """Compare single-trace baseline vs ``grade_operator`` for one operator."""
    op_u = op.upper()
    grade_st = single_trace_grader or grade_single_trace_baseline
    grade_hyper = hyper_grader or grade_operator

    conf = load_cassette_probe(op_u, "conforming", cassette)
    viol = load_cassette_probe(op_u, "violating", cassette)

    st_conf = grade_st(conf, twin_index=twin_index)
    st_viol = grade_st(viol, twin_index=twin_index)
    st_conf_v = str(st_conf.get("actual_verdict") or "")
    st_viol_v = str(st_viol.get("actual_verdict") or "")
    single_trace_equiv = st_conf_v == st_viol_v and st_conf_v in {"pass", "fail", "inconclusive"}

    hyper_conf = grade_hyper(conf)
    hyper_viol = grade_hyper(viol)
    # grade_operator returns GradeResult; tolerate dict-shaped test doubles.
    if hasattr(hyper_conf, "actual_verdict"):
        hyper_conf_v = str(hyper_conf.actual_verdict)
        hyper_conf_reason = str(getattr(hyper_conf, "reason", "") or "")
    else:
        hyper_conf_v = str(hyper_conf.get("actual_verdict") or "")  # type: ignore[union-attr]
        hyper_conf_reason = str(hyper_conf.get("reason") or "")  # type: ignore[union-attr]
    if hasattr(hyper_viol, "actual_verdict"):
        hyper_viol_v = str(hyper_viol.actual_verdict)
        hyper_viol_reason = str(getattr(hyper_viol, "reason", "") or "")
    else:
        hyper_viol_v = str(hyper_viol.get("actual_verdict") or "")  # type: ignore[union-attr]
        hyper_viol_reason = str(hyper_viol.get("reason") or "")  # type: ignore[union-attr]

    hyper_separates = hyper_conf_v == "pass" and hyper_viol_v == "fail"
    separates = bool(single_trace_equiv and hyper_separates)

    return {
        "operator": op_u,
        "single_trace": {
            "conforming_verdict": st_conf_v,
            "violating_verdict": st_viol_v,
            "conforming_reason": st_conf.get("reason"),
            "violating_reason": st_viol.get("reason"),
        },
        "hyperproperty": {
            "conforming_verdict": hyper_conf_v,
            "violating_verdict": hyper_viol_v,
            "conforming_reason": hyper_conf_reason,
            "violating_reason": hyper_viol_reason,
        },
        "single_trace_equiv": single_trace_equiv,
        "hyper_separates": hyper_separates,
        "separates": separates,
    }


def build_separation_report(
    *,
    cassette: Path | None = None,
    twin_index: int = DEFAULT_TWIN_INDEX,
    single_trace_grader: Callable[..., dict[str, Any]] | None = None,
    hyper_grader: Callable[[dict[str, Any]], Any] | None = None,
) -> dict[str, Any]:
    """Full-8 control-bank separation report (protocol scaffolding only)."""
    cassette_path = Path(cassette) if cassette is not None else DEFAULT_CASSETTE
    operators: dict[str, Any] = {}
    failures: list[str] = []
    separates_count = 0

    for op in OPERATORS:
        try:
            cell = evaluate_operator_separation(
                op,
                cassette=cassette_path,
                twin_index=twin_index,
                single_trace_grader=single_trace_grader,
                hyper_grader=hyper_grader,
            )
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            cell = {
                "operator": op,
                "single_trace_equiv": False,
                "hyper_separates": False,
                "separates": False,
                "error": str(exc),
            }
        operators[op] = cell
        if cell.get("separates"):
            separates_count += 1
        if not cell.get("hyper_separates"):
            failures.append(f"{op}:hyper_separates")
        if cell.get("error"):
            failures.append(f"{op}:error")

    n_ops = len(OPERATORS)
    control_separation_index = (separates_count / n_ops) if n_ops else 0.0
    if control_separation_index < 1.0:
        failures.append("control_separation_index<1.0")

    ok = not failures and control_separation_index >= 1.0
    return {
        "ok": ok,
        "operator_count": n_ops,
        "separates_count": separates_count,
        "control_separation_index": control_separation_index,
        "cassette": str(cassette_path.resolve()),
        "twin_index": twin_index,
        "operators": operators,
        "failures": failures,
        "notes": (
            "RQ1 protocol harness on handwritten cassette controls only. "
            "single_trace_equiv = same baseline verdict on conf+viol; "
            "hyper_separates = grade_operator conf=pass and viol=fail; "
            "separates = both; "
            "control_separation_index = separates_count/8 "
            "(structural control-bank fraction — NOT AUROC / model score / "
            "empirical RQ1 answer)."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    """CLI: ``python -m diptych.separation`` / ``diptych-separation``."""
    p = argparse.ArgumentParser(
        prog="diptych-separation",
        description=(
            "RQ1 protocol harness: single-trace baseline vs hyperproperty "
            "separation on handwritten cassette controls. Reports "
            "control_separation_index = separates_count/8 (structural "
            "control-bank fraction only — not AUROC / model score). "
            "Exit non-zero if any op fails hyper_separates or index < 1.0."
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
            "Which twin the single-trace baseline inspects "
            f"(default: {DEFAULT_TWIN_INDEX}; other twins ignored)"
        ),
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON report on stdout",
    )
    args = p.parse_args(list(argv) if argv is not None else None)

    report = build_separation_report(
        cassette=args.cassette,
        twin_index=args.twin_index,
    )
    if args.json:
        sys.stdout.write(json.dumps(report, indent=2) + "\n")
    else:
        print("== DIPTYCH separation (RQ1 protocol harness) ==")
        print(f"cassette={report['cassette']}")
        print(f"operator_count={report['operator_count']}")
        print(
            f"separates_count={report['separates_count']} "
            f"control_separation_index={report['control_separation_index']}"
        )
        print(
            "(control_separation_index = structural fraction on cassette "
            "controls — NOT AUROC / model score / empirical RQ1 answer)"
        )
        for op, cell in report["operators"].items():
            status = "OK  " if cell.get("separates") else "FAIL"
            print(
                f"  {status} {op:12} "
                f"single_trace_equiv={cell.get('single_trace_equiv')} "
                f"hyper_separates={cell.get('hyper_separates')} "
                f"separates={cell.get('separates')}"
            )
            if cell.get("error"):
                print(f"           error={cell['error']}")
        if report["ok"]:
            print("SEPARATION OK")
        else:
            print(f"SEPARATION FAIL ({', '.join(report['failures'])})")
    return EXIT_OK if report["ok"] else EXIT_FAIL


if __name__ == "__main__":
    raise SystemExit(main())
