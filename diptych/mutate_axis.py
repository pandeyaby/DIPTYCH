"""Axis-only mutations for DIPTYCH power-on-axis CI.

Each mutator edits *only* the operator's hyperproperty axis on a conforming
probe copy so the grader must flip pass → fail.
"""

from __future__ import annotations

import copy
from typing import Any, Callable

from diptych import OPERATORS

Mutator = Callable[[dict[str, Any]], dict[str, Any]]


def _deep(doc: dict[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(doc)


def mutate_reseed(doc: dict[str, Any]) -> dict[str, Any]:
    """Push stability beyond ε while keeping seeds different."""
    out = _deep(doc)
    t0, t1 = out["traces"][0], out["traces"][1]
    eps = float(t0["meta"].get("epsilon", 0.05))
    # Blow L∞ well past ε on trace b only
    vals = list(t1["channels"]["stability"]["values"])
    t1["channels"]["stability"]["values"] = [v + (eps + 1.0) for v in vals]
    out["meta"] = {**(out.get("meta") or {}), "axis_mutation": "stability_beyond_epsilon"}
    return out


def mutate_schemax(doc: dict[str, Any]) -> dict[str, Any]:
    """Rename/drop a required schema key on trace b."""
    out = _deep(doc)
    t1 = out["traces"][1]
    keys = list(t1["channels"]["schema"]["keys"])
    if not keys:
        raise ValueError("SCHEMAX mutation needs non-empty schema.keys")
    # Drop last required key and rename another if present
    dropped = keys.pop()
    if keys:
        keys[0] = f"{keys[0]}__renamed"
    t1["channels"]["schema"]["keys"] = keys
    out["meta"] = {
        **(out.get("meta") or {}),
        "axis_mutation": "schema_key_rename_drop",
        "dropped_key": dropped,
    }
    return out


def mutate_freezedry(doc: dict[str, Any]) -> dict[str, Any]:
    """Unfreeze rng/clock and break graded-series identity / fingerprint."""
    out = _deep(doc)
    t0, t1 = out["traces"][0], out["traces"][1]
    for tr in (t0, t1):
        tr["meta"]["freeze_channels"] = []
        tr["meta"]["frozen"] = False
    # Diverge graded series + fingerprint on b
    g1 = list(t1["channels"]["graded"]["values"])
    t1["channels"]["graded"]["values"] = [v + 0.5 for v in g1]
    t1["meta"]["decision_fingerprint"] = "sha256:leaked_unfrozen_fingerprint"
    out["meta"] = {**(out.get("meta") or {}), "axis_mutation": "unfreeze_and_diverge"}
    return out


def mutate_signflip(doc: dict[str, Any]) -> dict[str, Any]:
    """Break odd-symmetry on signflip_channel values."""
    out = _deep(doc)
    t0, t1 = out["traces"][0], out["traces"][1]
    target = t0["meta"]["signflip_channel"]
    # Make b equal to a (not -a) and break normalized fingerprint
    t1["channels"][target]["values"] = list(t0["channels"][target]["values"])
    t1["meta"]["sign_normalized_fingerprint"] = "sha256:broken_polarity"
    t0["meta"]["sign_normalized_fingerprint"] = "sha256:odd_symmetric_ok"
    out["meta"] = {**(out.get("meta") or {}), "axis_mutation": "break_odd_symmetry"}
    return out


def mutate_satextend(doc: dict[str, Any]) -> dict[str, Any]:
    """Push clipped values outside legal band; keep sat_lo/sat_hi."""
    out = _deep(doc)
    t0 = out["traces"][0]
    target = t0["meta"].get("sat_channel", "actuator")
    legal_hi = float(t0["meta"].get("legal_hi", t0["meta"]["sat_hi"]))
    sat_hi = float(t0["meta"]["sat_hi"])
    # Ensure sat bounds still contain the illegal value (widen sat if needed)
    illegal = legal_hi + 0.5
    if illegal > sat_hi:
        for tr in out["traces"]:
            tr["meta"]["sat_hi"] = illegal + 0.1
    vals = list(t0["channels"][target]["values"])
    vals[0] = illegal
    t0["channels"][target]["values"] = vals
    out["meta"] = {**(out.get("meta") or {}), "axis_mutation": "push_outside_legal_band"}
    return out


def mutate_histswap(doc: dict[str, Any]) -> dict[str, Any]:
    """Corrupt history at hist_splice_at (break cross-swap + mark corrupt)."""
    out = _deep(doc)
    t0, t1 = out["traces"][0], out["traces"][1]
    splice = int(t0["meta"].get("hist_splice_at", 0))
    h1 = list(t1["channels"]["history"]["values"])
    idx = min(max(splice, 0), len(h1) - 1)
    h1[idx] = 999.0
    t1["channels"]["history"]["values"] = h1
    # Break alt_history cross-link so cross-swap fails
    t0["channels"]["alt_history"]["values"] = [0.0] * len(t0["channels"]["alt_history"]["values"])
    t0["meta"]["history_corrupt"] = True
    t1["meta"]["history_corrupt"] = True
    out["meta"] = {
        **(out.get("meta") or {}),
        "axis_mutation": "corrupt_history_at_splice",
        "splice_index": idx,
    }
    return out


def mutate_trajswap(doc: dict[str, Any]) -> dict[str, Any]:
    """Break CRN shared noise and blow residual past bound; keep crn_closed_loop."""
    out = _deep(doc)
    assert out.get("coupling") == "crn_closed_loop"
    t0, t1 = out["traces"][0], out["traces"][1]
    # Keep coupling + crn flags; break shared noise by offsetting trajectory b
    traj_b = list(t1["channels"]["trajectory"]["values"])
    t1["channels"]["trajectory"]["values"] = [v + 3.0 for v in traj_b]
    # Also break swap cross-link and blow residual
    t0["channels"]["swapped_trajectory"]["values"] = list(t0["channels"]["trajectory"]["values"])
    bound = float(t0["meta"].get("residual_bound", 1.0))
    n = len(t0["channels"]["closed_loop_residual"]["values"])
    boom = [bound + 5.0] * n
    t0["channels"]["closed_loop_residual"]["values"] = boom
    t1["channels"]["closed_loop_residual"]["values"] = boom
    out["meta"] = {**(out.get("meta") or {}), "axis_mutation": "break_crn_and_residual"}
    return out


def mutate_varscale(doc: dict[str, Any]) -> dict[str, Any]:
    """Raise var_scale past bound; keep CRN values / mean-match honest."""
    out = _deep(doc)
    assert out.get("coupling") == "crn_closed_loop"
    t0, t1 = out["traces"][0], out["traces"][1]
    bound = float(t0["meta"].get("var_scale_bound", t1["meta"].get("var_scale_bound", 5.0)))
    # Honest mean-match: leave variance_proxy CRN twins untouched; only breach bound.
    hi = t0 if float(t0["meta"]["var_scale"]) >= float(t1["meta"]["var_scale"]) else t1
    hi["meta"]["var_scale"] = bound + 10.0
    out["meta"] = {**(out.get("meta") or {}), "axis_mutation": "var_scale_past_bound"}
    return out


MUTATORS: dict[str, Mutator] = {
    "RESEED": mutate_reseed,
    "SCHEMAX": mutate_schemax,
    "FREEZEDRY": mutate_freezedry,
    "SIGNFLIP": mutate_signflip,
    "SATEXTEND": mutate_satextend,
    "HISTSWAP": mutate_histswap,
    "TRAJSWAP": mutate_trajswap,
    "VARSCALE": mutate_varscale,
}

MUTATION_DESCRIPTIONS: dict[str, str] = {
    "RESEED": "inflate stability.values on twin B beyond meta.epsilon",
    "SCHEMAX": "rename/drop a required channels.schema.keys entry on twin B",
    "FREEZEDRY": "clear freeze_channels and diverge graded series + fingerprint",
    "SIGNFLIP": "replace -a with +a on signflip_channel; break sign_normalized_fingerprint",
    "SATEXTEND": "push a clipped value above legal_hi while keeping sat bounds covering it",
    "HISTSWAP": "corrupt history at hist_splice_at and break alt_history cross-link",
    "TRAJSWAP": "offset trajectory (break CRN noise) and blow closed_loop_residual past bound",
    "VARSCALE": "raise meta.var_scale past var_scale_bound (CRN values / mean-match honest)",
}


def mutate_axis(doc: dict[str, Any], operator: str | None = None) -> dict[str, Any]:
    """Return a deep-copied probe with only the operator axis mutated."""
    op = (operator or doc.get("operator") or "").upper()
    if op not in MUTATORS:
        raise KeyError(f"no axis mutator for {op!r}; known={list(MUTATORS)}")
    if op not in OPERATORS:
        raise KeyError(f"unknown operator {op!r}")
    return MUTATORS[op](doc)


def axis_fingerprint(doc: dict[str, Any]) -> str:
    """Hash graded axis payload (traces channels+meta). Ignores verdict cosmetics."""
    import hashlib
    import json

    payload = []
    for tr in doc.get("traces") or []:
        payload.append(
            {
                "channels": tr.get("channels"),
                "meta": tr.get("meta"),
            }
        )
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()


def cosmetic_verdict_only(doc: dict[str, Any]) -> dict[str, Any]:
    """Forbidden sole edit: flip expected_verdict without axis change (for negative tests)."""
    out = _deep(doc)
    out["expected_verdict"] = "fail" if doc.get("expected_verdict") == "pass" else "pass"
    return out


def cosmetic_sarif_level_rename(doc: dict[str, Any]) -> dict[str, Any]:
    """Forbidden sole edit: SARIF level rename without axis change (for negative tests)."""
    out = _deep(doc)
    # Top-level / envelope cosmetics only — must not touch traces channels+meta.
    out["sarif_level"] = "error" if doc.get("sarif_level") != "error" else "warning"
    out["sarif"] = {
        **(doc.get("sarif") or {}),
        "level": out["sarif_level"],
        "ruleId": (doc.get("sarif") or {}).get("ruleId", "diptych.cosmetic"),
    }
    return out


def cosmetic_auroc_inject(doc: dict[str, Any]) -> dict[str, Any]:
    """Forbidden sole edit: inject AUROC / fabricated score without axis change."""
    out = _deep(doc)
    out["auroc"] = 0.99
    out["fabricated_score"] = 0.99
    return out
