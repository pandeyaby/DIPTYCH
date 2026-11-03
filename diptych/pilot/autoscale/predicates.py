"""Requirement predicates for the autoscaling task.

Requirements paraphrase the Kubernetes Horizontal Pod Autoscaling
documentation; thresholds where the documentation gives none are ours. Each
predicate reads one live envelope and returns ``(verdict, reason, evidence)``.
"""

from __future__ import annotations

from typing import Any

from diptych.live import predicate, values

TASK = "autoscale"

SINGLE_RUN: tuple[str, ...] = ("tolerance", "scale-up-rate", "downscale-stabilization")
PAIRED: tuple[str, ...] = ("missing-metrics", "not-ready", "window-history", "ratio-only", "flapping")

# Section of the HPA documentation each requirement paraphrases.
DOC_SECTION = {
    "tolerance": "Algorithm details; Tolerance",
    "scale-up-rate": "Default behavior",
    "downscale-stabilization": "Algorithm details; Stabilization window",
    "missing-metrics": "Algorithm details",
    "not-ready": "Algorithm details",
    "window-history": "Stabilization window",
    "ratio-only": "Algorithm details",
    "flapping": "Stability of workload scale; Stabilization window",
}

RATIO_AGREE = 0.98     # fraction of ticks with equal decisions
FLAP_MARGIN = 2        # extra direction reversals allowed under noisy metrics

Result = tuple[str, str, dict[str, Any]]


def _reversals(xs: list[float]) -> int:
    signs = [1 if b > a else -1 for a, b in zip(xs, xs[1:]) if b != a]
    return sum(1 for a, b in zip(signs, signs[1:]) if a != b)


@predicate(TASK, "missing-metrics")
def missing_metrics(doc: dict[str, Any]) -> Result:
    """Twins, one tick from the same state: every metric present (a) / some
    ready pods' metrics withheld (b). Channel: [current, decision]."""
    n, da = values(doc, 0, "replicas")
    _, db = values(doc, 1, "replicas")
    ev = {"current": n, "all_metrics": da, "some_missing": db}
    ok = min(n, da) <= db <= max(n, da)
    return ("pass" if ok else "fail"), str(ev), ev


@predicate(TASK, "not-ready")
def not_ready(doc: dict[str, Any]) -> Result:
    """Twins, one tick from the same state: starting pods reported as not
    ready (a) / the same pods ready and idle (b)."""
    if doc["meta"].get("unready", 0) == 0:
        return "inconclusive", "no pods were starting at the fork", {}
    n, da = values(doc, 0, "replicas")
    _, db = values(doc, 1, "replicas")
    ev = {"current": n, "with_unready": da, "with_ready_idle": db}
    ok = da <= max(n, db)
    return ("pass" if ok else "fail"), str(ev), ev


@predicate(TASK, "window-history")
def window_history(doc: dict[str, Any]) -> Result:
    """Twins: demand differs only before t0 (a: a spike, b: none); decisions
    long after the stabilization window."""
    a, b = values(doc, 0, "replicas"), values(doc, 1, "replicas")
    diff = [i for i, (x, y) in enumerate(zip(a, b)) if x != y]
    ev = {"ticks_compared": len(a), "ticks_differing": len(diff), "first_difference": diff[0] if diff else None}
    return ("pass" if not diff else "fail"), str(ev), ev


@predicate(TASK, "ratio-only")
def ratio_only(doc: dict[str, Any]) -> Result:
    """Twins: target 0.6 (a) / target 0.9 with 1.5x demand (b); utilization
    over target is identical on every tick."""
    a, b = values(doc, 0, "replicas"), values(doc, 1, "replicas")
    agree = sum(x == y for x, y in zip(a, b)) / len(a)
    ev = {"agreement": agree, "max_abs_difference": max(abs(x - y) for x, y in zip(a, b))}
    return ("pass" if agree >= RATIO_AGREE else "fail"), str(ev), ev


@predicate(TASK, "flapping")
def flapping(doc: dict[str, Any]) -> Result:
    """Twins: steady demand, the same draws, low (a) / high (b) metric noise."""
    a, b = values(doc, 0, "replicas"), values(doc, 1, "replicas")
    ra, rb = _reversals(a), _reversals(b)
    ev = {"reversals_low_noise": ra, "reversals_high_noise": rb}
    return ("pass" if rb <= ra + FLAP_MARGIN else "fail"), str(ev), ev
