"""Substantive graders + cassette/fixture CLI for DIPTYCH operators.

Stranger / adapter path (no product imports)::

    python -m diptych.grade --input path/to/probe.json
    python -m diptych.grade --input path/to/fixtures/dir --with-axis-mutate
    python -m diptych.grade --input path/to/probe.json --sarif
    python -m diptych.grade --input path/to/probe.json --format sarif

Consumes CONTRACT v0.2 JSON from disk (as ZeroDay/AOMB would emit) and emits
a machine-readable grade report (JSON default; optional SARIF 2.1.0).
No AUROC / invented model scores.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from diptych import CRN_REQUIRED, SCHEMA
from diptych.contract import ContractError, validate_envelope
from diptych.crn import max_abs, mean, prove_traj, prove_varscale, variance

GRADE_SCHEMA = "1.0"
SARIF_VERSION = "2.1.0"
SARIF_SCHEMA_URI = "https://json.schemastore.org/sarif-2.1.0.json"

# Verdict → SARIF level (adapter consumers; no invented scores).
_VERDICT_LEVEL = {
    "pass": "none",
    "fail": "error",
    "inconclusive": "warning",
}


@dataclass
class GradeResult:
    operator: str
    probe_id: str
    control_role: str
    expected_verdict: str
    actual_verdict: str
    reason: str
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def matches_expected(self) -> bool:
        return self.actual_verdict == self.expected_verdict

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["matches_expected"] = self.matches_expected
        # Protocol field for honest inconclusive (ops/*/spec.yaml hooks).
        if self.actual_verdict == "inconclusive":
            d["meta"] = {"inconclusive_reason": self.reason}
        return d


def _vals(tr: dict, ch: str) -> list[float]:
    block = tr["channels"].get(ch)
    if not isinstance(block, dict) or "values" not in block:
        raise ContractError(f"missing channels.{ch}.values")
    return [float(x) for x in block["values"]]


def _res(doc, ok, reason, evidence=None):
    return GradeResult(
        operator=doc["operator"], probe_id=doc["probe_id"],
        control_role=doc["control_role"], expected_verdict=doc["expected_verdict"],
        actual_verdict="pass" if ok else "fail", reason=reason, evidence=evidence or {},
    )


def _inconclusive(doc, reason, evidence=None):
    return GradeResult(
        operator=doc["operator"],
        probe_id=doc["probe_id"],
        control_role=doc["control_role"],
        expected_verdict=doc["expected_verdict"],
        actual_verdict="inconclusive",
        reason=reason,
        evidence=evidence or {},
    )


def comparability_reason(doc: dict[str, Any]) -> str | None:
    """Return reason when paired traces are not comparable (ops/*/spec.yaml).

    Documented ``inconclusive_when`` hooks (honest, not invented):
    - missing twin role
    - traces incomparable under shared prefix rule (paired value-length mismatch)
    - CRN stream-id mismatch (closed-loop twins not coupled)

    Horizon length alone does not force inconclusive: fixtures may carry
    shorter auxiliary channels (e.g. HISTSWAP ``present``) while the axis
    series remains paired. Graders compare the shared twin window as-is.
    """
    traces = doc.get("traces") or []
    if not isinstance(traces, list) or len(traces) < 2:
        return "missing twin role"

    t0, t1 = traces[0], traces[1]
    if not isinstance(t0, dict) or not isinstance(t1, dict):
        return "missing twin role"

    ch0 = t0.get("channels") if isinstance(t0.get("channels"), dict) else {}
    ch1 = t1.get("channels") if isinstance(t1.get("channels"), dict) else {}

    names = set(ch0) | set(ch1)
    for name in sorted(names):
        b0 = ch0.get(name) if isinstance(ch0.get(name), dict) else {}
        b1 = ch1.get(name) if isinstance(ch1.get(name), dict) else {}
        has_v0 = "values" in b0
        has_v1 = "values" in b1
        if not has_v0 and not has_v1:
            continue  # key-set channels (e.g. SCHEMAX) are not series-compared here
        if has_v0 != has_v1:
            return (
                "traces incomparable under shared prefix rule: "
                f"channel {name!r} missing values twin"
            )
        v0, v1 = b0.get("values"), b1.get("values")
        if not isinstance(v0, list) or not isinstance(v1, list):
            return (
                "traces incomparable under shared prefix rule: "
                f"channel {name!r} values not lists"
            )
        if len(v0) != len(v1):
            return (
                "traces incomparable under shared prefix rule: "
                f"channel {name!r} length mismatch ({len(v0)}!={len(v1)})"
            )

    op = str(doc.get("operator", "")).upper()
    if op in CRN_REQUIRED:
        m0 = t0.get("meta") if isinstance(t0.get("meta"), dict) else {}
        m1 = t1.get("meta") if isinstance(t1.get("meta"), dict) else {}
        if m0.get("crn_stream_id") != m1.get("crn_stream_id"):
            return (
                "traces incomparable under shared prefix rule: "
                "crn_stream_id mismatch"
            )

    return None


def grade_reseed(doc):
    t0, t1 = doc["traces"][0], doc["traces"][1]
    eps = float(t0["meta"].get("epsilon", 0.05))
    a, b = _vals(t0, "stability"), _vals(t1, "stability")
    if t0["meta"]["seed"] == t1["meta"]["seed"]:
        return _res(doc, False, "seeds must differ")
    if t0["meta"].get("config_id") != t1["meta"].get("config_id"):
        return _res(doc, False, "config_id mismatch")
    diff = max_abs(a, b)
    return _res(doc, diff <= eps, f"Linf={diff} eps={eps}", {"max_abs": diff, "epsilon": eps})


def grade_schemax(doc):
    t0, t1 = doc["traces"][0], doc["traces"][1]
    def keys(tr):
        ch = tr["channels"].get("schema")
        if isinstance(ch, dict) and isinstance(ch.get("keys"), list):
            return set(map(str, ch["keys"]))
        req = tr["meta"].get("required_schema_keys")
        if isinstance(req, list):
            return set(map(str, req))
        raise ContractError("schema keys missing")
    k0, k1 = keys(t0), keys(t1)
    required = set()
    for tr in (t0, t1):
        req = tr["meta"].get("required_schema_keys")
        if isinstance(req, list):
            required |= set(map(str, req))
    ok = k0 == k1 and (not required or k0 == required)
    return _res(doc, ok, "keysets equal" if ok else "keyset mismatch",
                {"a": sorted(k0), "b": sorted(k1), "required": sorted(required)})


def grade_freezedry(doc):
    t0, t1 = doc["traces"][0], doc["traces"][1]
    fp0 = t0["meta"].get("decision_fingerprint")
    fp1 = t1["meta"].get("decision_fingerprint")
    f0 = set(t0["meta"].get("freeze_channels") or [])
    f1 = set(t1["meta"].get("freeze_channels") or [])
    frozen = bool(f0 & {"rng", "clock"}) and bool(f1 & {"rng", "clock"})
    # Graded series (AOMB sketch): identical under freeze; diverge when rng/clock leak
    g0 = _vals(t0, "graded")
    g1 = _vals(t1, "graded")
    series_identical = len(g0) == len(g1) and max_abs(g0, g1) <= 1e-12
    fp_identical = fp0 is not None and fp0 == fp1
    ok = frozen and series_identical and fp_identical
    return _res(
        doc,
        ok,
        f"frozen={frozen} series_identical={series_identical} fp_identical={fp_identical}",
        {
            "fp0": fp0,
            "fp1": fp1,
            "freeze0": sorted(f0),
            "freeze1": sorted(f1),
            "series_identical": series_identical,
            "series_max_abs": max_abs(g0, g1),
        },
    )


def grade_signflip(doc):
    t0, t1 = doc["traces"][0], doc["traces"][1]
    target = t0["meta"].get("signflip_channel") or t1["meta"].get("signflip_channel")
    if not target:
        return _res(doc, False, "signflip_channel missing")
    a, b = _vals(t0, target), _vals(t1, target)
    if len(a) != len(b):
        # Prefer comparability_reason; keep defensive fail if grader called directly.
        return _res(doc, False, "length mismatch")
    err = max(abs(a[i] + b[i]) for i in range(len(a)))
    eps = float(t0["meta"].get("signflip_eps", 1e-9))
    fp0 = t0["meta"].get("sign_normalized_fingerprint")
    fp1 = t1["meta"].get("sign_normalized_fingerprint")
    ok = err <= eps or (fp0 is not None and fp0 == fp1)
    return _res(doc, ok, f"odd_err={err} fp_equal={fp0 == fp1}", {"odd_err": err, "eps": eps})


def grade_satextend(doc):
    t0, t1 = doc["traces"][0], doc["traces"][1]
    target = t0["meta"].get("sat_channel", "actuator")
    lo, hi = float(t0["meta"]["sat_lo"]), float(t0["meta"]["sat_hi"])
    legal_lo = float(t0["meta"].get("legal_lo", lo))
    legal_hi = float(t0["meta"].get("legal_hi", hi))
    vals = _vals(t0, target) + _vals(t1, target)
    in_sat = all(lo <= v <= hi for v in vals)
    in_legal = all(legal_lo <= v <= legal_hi for v in vals)
    return _res(doc, in_sat and in_legal, f"in_sat={in_sat} in_legal={in_legal}",
                {"min": min(vals), "max": max(vals), "sat": [lo, hi], "legal": [legal_lo, legal_hi]})


def grade_histswap(doc):
    t0, t1 = doc["traces"][0], doc["traces"][1]
    h0, h1 = _vals(t0, "history"), _vals(t1, "history")
    a0, a1 = _vals(t0, "alt_history"), _vals(t1, "alt_history")
    p0, p1 = _vals(t0, "present"), _vals(t1, "present")
    if len(h0) != len(h1):
        return _res(doc, False, "history length mismatch")
    cross = max_abs(h0, a1) <= 1e-9 and max_abs(h1, a0) <= 1e-9
    present_ok = max_abs(p0, p1) <= 1e-9
    corrupt = bool(t0["meta"].get("history_corrupt") or t1["meta"].get("history_corrupt"))
    ok = cross and present_ok and not corrupt
    return _res(doc, ok, f"cross={cross} present={present_ok} corrupt={corrupt}",
                {"cross": cross, "present_ok": present_ok, "corrupt": corrupt})


def grade_trajswap(doc):
    t0, t1 = doc["traces"][0], doc["traces"][1]
    if t0["meta"].get("crn_stream_id") != t1["meta"].get("crn_stream_id"):
        return _res(doc, False, "crn_stream_id mismatch")
    if not (t0["meta"].get("crn_closed_loop") and t1["meta"].get("crn_closed_loop")):
        return _res(doc, False, "crn_closed_loop required on traces")
    p0, p1 = _vals(t0, "trajectory"), _vals(t1, "trajectory")
    s0, s1 = _vals(t0, "swapped_trajectory"), _vals(t1, "swapped_trajectory")
    r0, r1 = _vals(t0, "closed_loop_residual"), _vals(t1, "closed_loop_residual")
    drift_a, drift_b = float(t0["meta"]["crn_drift"]), float(t1["meta"]["crn_drift"])
    bound = float(t0["meta"].get("residual_bound", 1.0))
    crn_ok, ev = prove_traj(p0, p1, drift_a=drift_a, drift_b=drift_b)
    cross = max_abs(s0, p1) <= 1e-9 and max_abs(s1, p0) <= 1e-9
    resid_ok = all(r <= bound for r in (r0 + r1))
    ok = crn_ok and cross and resid_ok
    return _res(doc, ok, f"crn={crn_ok} cross={cross} resid_ok={resid_ok}",
                {**ev, "cross": cross, "resid_ok": resid_ok, "bound": bound})


def grade_varscale(doc):
    t0, t1 = doc["traces"][0], doc["traces"][1]
    if t0["meta"].get("crn_stream_id") != t1["meta"].get("crn_stream_id"):
        return _res(doc, False, "crn_stream_id mismatch")
    if not (t0["meta"].get("crn_closed_loop") and t1["meta"].get("crn_closed_loop")):
        return _res(doc, False, "crn_closed_loop required")
    a, b = _vals(t0, "variance_proxy"), _vals(t1, "variance_proxy")
    sa, sb = float(t0["meta"]["var_scale"]), float(t1["meta"]["var_scale"])
    mean_v = float(t0["meta"].get("crn_mean", t1["meta"]["crn_mean"]))
    bound = float(t0["meta"].get("var_scale_bound", 5.0))
    if sa > sb:
        a, b, sa, sb = b, a, sb, sa
    crn_ok, ev = prove_varscale(a, b, mean_v=mean_v, scale_a=sa, scale_b=sb)
    mean_eps = float(t0["meta"].get("mean_match_eps", 1e-4))
    # CRN twins share generative mean; sample means differ by (sb-sa)*mean(z).
    # Mean-match = each series is consistent with declared crn_mean under its scale.
    mean_matched = (
        abs(mean(a) - mean_v) <= abs(sa) * 2.0 + mean_eps
        and abs(mean(b) - mean_v) <= abs(sb) * 2.0 + mean_eps
    )
    ordering = variance(b) > variance(a) and sb > sa
    within = sb <= bound and sa <= bound
    ok = crn_ok and mean_matched and ordering and within
    return _res(doc, ok, f"crn={crn_ok} mean={mean_matched} ord={ordering} within={within}",
                {**ev, "mean_matched": mean_matched, "ordering": ordering, "within": within,
                 "sample_mean_a": mean(a), "sample_mean_b": mean(b), "crn_mean": mean_v})


GRADERS: dict[str, Callable] = {
    "SIGNFLIP": grade_signflip,
    "TRAJSWAP": grade_trajswap,
    "VARSCALE": grade_varscale,
    "SATEXTEND": grade_satextend,
    "HISTSWAP": grade_histswap,
    "FREEZEDRY": grade_freezedry,
    "RESEED": grade_reseed,
    "SCHEMAX": grade_schemax,
}


def grade_document(doc: dict) -> GradeResult:
    doc = validate_envelope(doc)
    reason = comparability_reason(doc)
    if reason:
        return _inconclusive(doc, reason, {"comparability": reason})
    g = GRADERS[doc["operator"]]
    if g.__code__.co_code == (lambda: True).__code__.co_code:  # noqa: E731
        raise ContractError(f"stub grader {doc['operator']}")
    return g(doc)


# ---------------------------------------------------------------------------
# Cassette / fixture ingest CLI (disk JSON → grade report)
# ---------------------------------------------------------------------------


def collect_probe_paths(input_path: Path) -> list[Path]:
    """Resolve a file or directory of CONTRACT JSON probes."""
    if not input_path.exists():
        raise FileNotFoundError(f"input not found: {input_path}")
    if input_path.is_file():
        return [input_path]
    if input_path.is_dir():
        paths = sorted(
            p for p in input_path.rglob("*.json")
            if p.is_file() and not p.name.startswith(".")
        )
        return paths
    raise FileNotFoundError(f"input not a file or directory: {input_path}")


def _grade_entry(
    path: Path,
    *,
    with_axis_mutate: bool = False,
    root: Path | None = None,
) -> dict[str, Any]:
    """Grade one on-disk probe; never imports product code."""
    rel = str(path if root is None else path.relative_to(root) if path.is_relative_to(root) else path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {
            "path": rel,
            "status": "rejected",
            "error": f"invalid JSON: {exc}",
            "diptych_schema": None,
            "operator": None,
            "actual_verdict": None,
            "expected_verdict": None,
            "matches_expected": False,
        }
    except OSError as exc:
        return {
            "path": rel,
            "status": "rejected",
            "error": f"read error: {exc}",
            "diptych_schema": None,
            "operator": None,
            "actual_verdict": None,
            "expected_verdict": None,
            "matches_expected": False,
        }

    try:
        from diptych.api import grade_operator, parse_probe, run_gate_axis_mutate

        env = parse_probe(raw)
        result = grade_operator(env)
    except ContractError as exc:
        return {
            "path": rel,
            "status": "rejected",
            "error": str(exc),
            "diptych_schema": raw.get("diptych_schema") if isinstance(raw, dict) else None,
            "operator": (raw.get("operator") if isinstance(raw, dict) else None),
            "actual_verdict": None,
            "expected_verdict": raw.get("expected_verdict") if isinstance(raw, dict) else None,
            "matches_expected": False,
        }

    entry: dict[str, Any] = {
        "path": rel,
        "status": "graded",
        "diptych_schema": env.diptych_schema,
        "schema_version": env.diptych_schema,
        "source": env.source,
        "operator": result.operator,
        "probe_id": result.probe_id,
        "control_role": result.control_role,
        "coupling": env.coupling,
        "expected_verdict": result.expected_verdict,
        "actual_verdict": result.actual_verdict,
        "matches_expected": result.matches_expected,
        "reason": result.reason,
        "evidence": result.evidence,
    }
    if result.actual_verdict == "inconclusive":
        entry["meta"] = {"inconclusive_reason": result.reason}
    if env.horizon is not None:
        entry["horizon"] = env.horizon.to_dict()
    if with_axis_mutate and env.control_role == "conforming" and result.actual_verdict == "pass":
        ok, evidence = run_gate_axis_mutate(env.operator, env)
        entry["gate_axis_mutate"] = {
            "power_ok": ok,
            "baseline_verdict": evidence.get("baseline_verdict"),
            "mutated_verdict": evidence.get("mutated_verdict"),
            "semantic_witness": evidence.get("semantic_witness"),
            "failures": evidence.get("failures") or [],
            "probe_tree_branch": evidence.get("probe_tree_branch"),
            "amortization": evidence.get("amortization"),
        }
    elif with_axis_mutate:
        entry["gate_axis_mutate"] = {
            "power_ok": None,
            "skipped": True,
            "reason": "axis mutate only runs on conforming probes that grade pass",
        }
    return entry


def build_grade_report(
    input_path: str | Path,
    *,
    with_axis_mutate: bool = False,
) -> dict[str, Any]:
    """Ingest probe JSON from disk (file or directory) → grade report dict."""
    root = Path(input_path).resolve()
    paths = collect_probe_paths(root)
    results = [
        _grade_entry(p, with_axis_mutate=with_axis_mutate, root=root if root.is_dir() else root.parent)
        for p in paths
    ]
    graded = [r for r in results if r.get("status") == "graded"]
    rejected = [r for r in results if r.get("status") == "rejected"]
    mismatches = [r for r in graded if not r.get("matches_expected")]
    ok = bool(paths) and not rejected and not mismatches
    return {
        "grade_schema": GRADE_SCHEMA,
        "diptych_schema": SCHEMA,
        "ok": ok,
        "input": str(root),
        "count": len(results),
        "graded": len(graded),
        "rejected": len(rejected),
        "mismatches": len(mismatches),
        "results": results,
    }


def _sarif_level_for_entry(entry: dict[str, Any]) -> str:
    """Map graded/rejected entry → SARIF level (pass/fail/inconclusive/reject)."""
    if entry.get("status") == "rejected":
        return "error"
    verdict = entry.get("actual_verdict")
    if isinstance(verdict, str) and verdict in _VERDICT_LEVEL:
        return _VERDICT_LEVEL[verdict]
    return "error"


def _sarif_rule_id_for_entry(entry: dict[str, Any]) -> str:
    if entry.get("status") == "rejected":
        return "diptych.grade.rejected"
    verdict = entry.get("actual_verdict")
    if verdict == "pass":
        return "diptych.grade.pass"
    if verdict == "fail":
        return "diptych.grade.fail"
    if verdict == "inconclusive":
        return "diptych.grade.inconclusive"
    return "diptych.grade.rejected"


def report_to_sarif(report: dict[str, Any]) -> dict[str, Any]:
    """Map a grade report into SARIF 2.1.0 (adapter export; no invented scores).

    Levels/rules encode graded verdicts only:
    - pass → level ``none``, rule ``diptych.grade.pass``
    - fail → level ``error``, rule ``diptych.grade.fail``
    - inconclusive → level ``warning``, rule ``diptych.grade.inconclusive``
    - rejected / thin envelope → level ``error``, rule ``diptych.grade.rejected``
    """
    results: list[dict[str, Any]] = []
    for entry in report.get("results") or []:
        if not isinstance(entry, dict):
            continue
        rule_id = _sarif_rule_id_for_entry(entry)
        level = _sarif_level_for_entry(entry)
        status = entry.get("status")
        verdict = entry.get("actual_verdict")
        path = entry.get("path")
        if status == "rejected":
            msg = (
                f"rejected {path}: {entry.get('error')}"
            )
        else:
            msg = (
                f"{entry.get('operator')}/{entry.get('control_role')}: "
                f"expected={entry.get('expected_verdict')} actual={verdict} "
                f"matches_expected={entry.get('matches_expected')} "
                f"({entry.get('reason')})"
            )
        props: dict[str, Any] = {
            "path": path,
            "status": status,
            "operator": entry.get("operator"),
            "probe_id": entry.get("probe_id"),
            "control_role": entry.get("control_role"),
            "expected_verdict": entry.get("expected_verdict"),
            "actual_verdict": verdict,
            "matches_expected": entry.get("matches_expected"),
            "diptych_schema": entry.get("diptych_schema"),
        }
        if entry.get("error") is not None:
            props["error"] = entry.get("error")
        if entry.get("reason") is not None:
            props["reason"] = entry.get("reason")
        loc: dict[str, Any] | None = None
        if isinstance(path, str) and path:
            loc = {
                "physicalLocation": {
                    "artifactLocation": {"uri": path},
                }
            }
        result: dict[str, Any] = {
            "ruleId": rule_id,
            "level": level,
            "message": {"text": msg},
            "properties": props,
        }
        if loc is not None:
            result["locations"] = [loc]
        results.append(result)

    rules = [
        {
            "id": "diptych.grade.pass",
            "shortDescription": {"text": "Graded verdict: pass"},
            "fullDescription": {
                "text": (
                    "Probe graded pass under CONTRACT v0.2. "
                    "Not AUROC or model quality."
                )
            },
            "defaultConfiguration": {"level": "none"},
        },
        {
            "id": "diptych.grade.fail",
            "shortDescription": {"text": "Graded verdict: fail"},
            "fullDescription": {
                "text": (
                    "Probe graded fail under CONTRACT v0.2. "
                    "Not AUROC or model quality."
                )
            },
            "defaultConfiguration": {"level": "error"},
        },
        {
            "id": "diptych.grade.inconclusive",
            "shortDescription": {"text": "Graded verdict: inconclusive"},
            "fullDescription": {
                "text": (
                    "Honest inconclusive (comparability / missing twin). "
                    "inconclusive ≠ green."
                )
            },
            "defaultConfiguration": {"level": "warning"},
        },
        {
            "id": "diptych.grade.rejected",
            "shortDescription": {"text": "Envelope rejected (thin/stub/schema)"},
            "fullDescription": {
                "text": (
                    "Loud reject: missing axis, stub markers, hardcoded_pass, "
                    "forbidden score fields, or wrong schema version."
                )
            },
            "defaultConfiguration": {"level": "error"},
        },
    ]

    return {
        "$schema": SARIF_SCHEMA_URI,
        "version": SARIF_VERSION,
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "diptych.grade",
                        "informationUri": "https://github.com/pandeyaby/DIPTYCH",
                        "version": GRADE_SCHEMA,
                        "rules": rules,
                    }
                },
                "results": results,
                "properties": {
                    "grade_schema": report.get("grade_schema"),
                    "diptych_schema": report.get("diptych_schema"),
                    "ok": report.get("ok"),
                    "input": report.get("input"),
                    "count": report.get("count"),
                    "graded": report.get("graded"),
                    "rejected": report.get("rejected"),
                    "mismatches": report.get("mismatches"),
                },
            }
        ],
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="python -m diptych.grade",
        description=(
            "Grade CONTRACT v0.2 probe JSON from disk "
            "(adapter cassette/fixture ingest; no product imports). "
            "JSON default; optional SARIF 2.1.0 via --sarif / --format sarif."
        ),
    )
    p.add_argument(
        "--input", "-i",
        required=True,
        help="Path to a probe.json file or a directory of probes",
    )
    p.add_argument(
        "--with-axis-mutate",
        action="store_true",
        help="Also run gate_axis_mutate on conforming probes that grade pass",
    )
    p.add_argument(
        "--format",
        choices=("json", "sarif"),
        default="json",
        help="Output format (default: json)",
    )
    p.add_argument(
        "--sarif",
        action="store_true",
        help="Emit SARIF 2.1.0 (alias for --format sarif)",
    )
    p.add_argument(
        "--output", "-o",
        default=None,
        help="Optional path to write the report (default: stdout)",
    )
    args = p.parse_args(argv)
    fmt = "sarif" if args.sarif else args.format

    try:
        report = build_grade_report(args.input, with_axis_mutate=args.with_axis_mutate)
    except FileNotFoundError as exc:
        err = {
            "grade_schema": GRADE_SCHEMA,
            "diptych_schema": SCHEMA,
            "ok": False,
            "error": str(exc),
            "results": [],
        }
        if fmt == "sarif":
            payload: dict[str, Any] = report_to_sarif(err)
        else:
            payload = err
        text = json.dumps(payload, indent=2, sort_keys=False) + "\n"
        sys.stderr.write(text)
        return 2

    if fmt == "sarif":
        payload = report_to_sarif(report)
    else:
        payload = report
    text = json.dumps(payload, indent=2, sort_keys=False) + "\n"
    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
