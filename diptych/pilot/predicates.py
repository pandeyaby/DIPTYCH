"""Requirement predicates for the concurrency-control task (TASK.md).

Each predicate reads one live envelope (``diptych.live``) and returns
``(verdict, reason, evidence)``. Nothing here executes a controller: the
verdict is a function of the recorded pair, so saved envelopes re-grade to
the same result.
"""

from __future__ import annotations

from typing import Any

from diptych.live import predicate, values

TASK = "concurrency"
BOUND_EPS = 1e-9
PAIR_RTOL = 1e-3
SAT_RTOL = 0.05  # estimators still settling on the short arm

REQUIREMENT = {
    "SIGNFLIP": "asymmetric-response",
    "TRAJSWAP": "trend-recency",
    "VARSCALE": "volatility-suppression",
    "SATEXTEND": "anti-windup",
    "HISTSWAP": "setpoint-change-quiet",
    "FREEZEDRY": "round-trip-stability",
    "RESEED": "determinism",
    "SCHEMAX": "schema-stability",
}

Result = tuple[str, str, dict[str, Any]]


def _bounds(doc: dict[str, Any]) -> tuple[float, float]:
    return float(doc["meta"]["min_limit"]), float(doc["meta"]["max_limit"])


def _at_bound(doc: dict[str, Any], x: float) -> bool:
    lo, hi = _bounds(doc)
    return x <= lo + BOUND_EPS or x >= hi - BOUND_EPS


def _strictly_greater(a: float, b: float) -> bool:
    """``a > b`` by more than float noise."""
    return a - b > PAIR_RTOL * max(1.0, abs(a), abs(b))


def _close(a: float, b: float, rtol: float = PAIR_RTOL) -> bool:
    return abs(a - b) <= rtol * max(1.0, abs(a), abs(b))


@predicate(TASK, "asymmetric-response")
def asymmetric_response(doc: dict[str, Any]) -> Result:
    """Twins: one tick at +20% (a) and -20% (b) latency from the same state."""
    a, b = values(doc, 0, "limit"), values(doc, 1, "limit")
    last = a[0]
    if a[0] != b[0]:
        return "inconclusive", "shared prefix broken: branches start from different limits", {}
    if _at_bound(doc, last):
        return "inconclusive", "fork state at a limit bound", {"reason": "fork state at a limit bound"}
    down, up = last - a[1], b[1] - last
    ok = down > 0 and down > 2 * up
    return ("pass" if ok else "fail"), f"down={down} up={up}", {"down": down, "up": up}


@predicate(TASK, "trend-recency")
def trend_recency(doc: dict[str, Any]) -> Result:
    """Twins: the same latencies in improving (a) and worsening (b) order."""
    a, b = values(doc, 0, "limit"), values(doc, 1, "limit")
    if any(_at_bound(doc, x) for x in a + b):
        return "inconclusive", "branch hit a limit bound", {"reason": "branch hit a limit bound"}
    ev = {"improving_end": a[-1], "worsening_end": b[-1]}
    return ("pass" if _strictly_greater(a[-1], b[-1]) else "fail"), str(ev), ev


@predicate(TASK, "volatility-suppression")
def volatility_suppression(doc: dict[str, Any]) -> Result:
    """Twins: closed loop under the same draws at variance scale 1 (a) and 3 (b)."""
    a, b = values(doc, 0, "limit"), values(doc, 1, "limit")
    half = len(a) // 2
    ma = sum(a[half:]) / max(1, len(a) - half)
    mb = sum(b[half:]) / max(1, len(b) - half)
    ev = {"mean_limit_low_var": ma, "mean_limit_high_var": mb}
    return ("pass" if _strictly_greater(ma, mb) else "fail"), str(ev), ev


@predicate(TASK, "anti-windup")
def anti_windup(doc: dict[str, Any]) -> Result:
    """Twins: limit before/after release, after short (a) and long (b) saturation."""
    _, hi = _bounds(doc)
    if doc["meta"].get("search_max_limit", hi) < hi - BOUND_EPS:
        why = "controller never saturates at max_limit"
        return "inconclusive", why, {"reason": why}
    a, b = values(doc, 0, "release"), values(doc, 1, "release")
    if a[0] < hi - BOUND_EPS or b[0] < hi - BOUND_EPS:
        why = "saturation not sustained on both arms"
        return "inconclusive", why, {"reason": why}
    da, db = hi - a[1], hi - b[1]
    ev = {"short_arm_drop": da, "long_arm_drop": db, "k0": doc["meta"].get("k0")}
    return ("pass" if _close(da, db, SAT_RTOL) else "fail"), str(ev), ev


@predicate(TASK, "setpoint-change-quiet")
def setpoint_change_quiet(doc: dict[str, Any]) -> Result:
    """Twins: limits from the last pre-step tick through six post-step ticks,
    after high (a) and low (b) pre-step latency histories."""
    a, b = values(doc, 0, "limit"), values(doc, 1, "limit")
    if any(_at_bound(doc, x) for x in a + b):
        return "inconclusive", "branch hit a limit bound", {"reason": "branch hit a limit bound"}
    da = [x - y for x, y in zip(a[1:], a[:-1])]
    db = [x - y for x, y in zip(b[1:], b[:-1])]
    ra = [x / y for x, y in zip(a[1:], a[:-1])]
    rb = [x / y for x, y in zip(b[1:], b[:-1])]
    same = all(_close(x, y) for x, y in zip(da, db)) or all(_close(x, y) for x, y in zip(ra, rb))
    ev = {"corrections_a": da, "corrections_b": db}
    return ("pass" if same else "fail"), "post-step corrections compared", ev


def _digests_equal(doc: dict[str, Any], label: str) -> Result:
    digests = [tr["meta"]["decision_digest"] for tr in doc["traces"]]
    same = len(set(digests)) == 1
    return ("pass" if same else "fail"), f"{label}={same}", {label: same}


@predicate(TASK, "determinism")
def determinism(doc: dict[str, Any]) -> Result:
    """Twins: the same run in fresh processes with different hash/global seeds."""
    return _digests_equal(doc, "identical")


@predicate(TASK, "round-trip-stability")
def round_trip_stability(doc: dict[str, Any]) -> Result:
    """Twins: uninterrupted continuation (a) vs. continuation after restore (b)."""
    return _digests_equal(doc, "identical_after_restore")


@predicate(TASK, "schema-stability")
def schema_stability(doc: dict[str, Any]) -> Result:
    """Traces: the distinct telemetry key sets seen in each scenario."""
    keysets = {k for tr in doc["traces"] for k in tr["channels"]["schema"]["keys"]}
    ev = {"distinct_keysets": len(keysets)}
    return ("pass" if len(keysets) == 1 else "fail"), str(ev), ev
