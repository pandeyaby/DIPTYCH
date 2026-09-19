"""Substantive graders for all 8 DIPTYCH operators (fixture-aligned)."""
from __future__ import annotations
from dataclasses import asdict, dataclass, field
from typing import Any, Callable
from diptych.contract import ContractError, validate_envelope
from diptych.crn import max_abs, mean, prove_traj, prove_varscale, variance

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
    g = GRADERS[doc["operator"]]
    if g.__code__.co_code == (lambda: True).__code__.co_code:  # noqa: E731
        raise ContractError(f"stub grader {doc['operator']}")
    return g(doc)
