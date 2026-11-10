"""Horizontal pod autoscaler controller.

Implements the Kubernetes HPA algorithm: a base recommendation of
``ceil(sum(utilization) / target_utilization)`` over ready pods with a
known metric, held steady within a tolerance band around the target,
damped when metrics are missing (treating missing ready pods as 0% of
target for a scale-up decision and as exactly on-target for a scale-down
decision, per the Kubernetes "Algorithm details" documentation), capped so
that pods still starting cannot enlarge a scale-up beyond what they would
justify if already ready at 0% utilization, bounded below while scaling
down by the highest base recommendation seen in the stabilization window,
and finally bounded by the scale-up rate limit and replica bounds.
"""

from __future__ import annotations

import math
from collections import deque


def _compute_base(k: int, s: float, current: int, target: float, tol: float) -> float:
    """Base recommendation for ``k`` pods summing to utilization ``s``.

    Returns ``current`` unchanged (as a float) when the average utilization
    is within ``tol`` of ``target``, and otherwise ``ceil(s / target)``.
    """
    if k <= 0:
        return float(current)
    ratio = (s / k) / target
    if abs(ratio - 1.0) <= tol:
        return float(current)
    return float(math.ceil(s / target))


def _recommend(k_known: int, s_known: float, k_missing: int, current: int,
               target: float, tol: float) -> float:
    """Recommendation given known-metric pods plus a count of pods whose
    metric is withheld, damping the missing ones conservatively depending
    on which direction the known-only data points."""
    raw = _compute_base(k_known, s_known, current, target, tol)
    if k_missing <= 0:
        return raw
    if raw > current:
        # Scale-up direction: assume missing pods are idle (0% of target),
        # which can only pull the recommendation back down towards current.
        k2, s2 = k_known + k_missing, s_known
    elif raw < current:
        # Scale-down direction: assume missing pods sit exactly on target,
        # which can only pull the recommendation back up towards current.
        k2, s2 = k_known + k_missing, s_known + k_missing * target
    else:
        return raw
    return _compute_base(k2, s2, current, target, tol)


class Controller:
    def __init__(self, config: dict):
        self.min_replicas = int(config["min_replicas"])
        self.max_replicas = int(config["max_replicas"])
        self.target_utilization = float(config["target_utilization"])
        self.tolerance = float(config["tolerance"])
        self.stabilization_ticks = int(config["stabilization_ticks"])
        self._history: deque[float] = deque(maxlen=max(1, self.stabilization_ticks))

    def tick(self, obs: dict) -> dict:
        current = int(obs["replicas"])
        target = self.target_utilization
        tol = self.tolerance

        k_known = 0
        s_known = 0.0
        k_missing = 0
        k_notready = 0
        for pod in obs["pods"]:
            if not pod["ready"]:
                k_notready += 1
            elif pod["utilization"] is None:
                k_missing += 1
            else:
                k_known += 1
                s_known += pod["utilization"]

        raw = _compute_base(k_known, s_known, current, target, tol)
        self._history.append(raw)
        window_max = max(self._history)

        primary = _recommend(k_known, s_known, k_missing, current, target, tol)

        if k_notready > 0:
            alt = _recommend(k_known + k_notready, s_known, k_missing, current, target, tol)
            cap = max(alt, current)
            candidate = min(primary, cap)
        else:
            candidate = primary

        if candidate < current:
            candidate = max(candidate, window_max)

        decision = int(math.ceil(candidate - 1e-9))

        if decision > current:
            decision = min(decision, current + max(4, current))

        decision = min(self.max_replicas, max(self.min_replicas, decision))

        telemetry = {
            "raw_recommendation": raw,
            "primary_recommendation": primary,
            "window_max": window_max,
            "k_known": k_known,
            "k_missing": k_missing,
            "k_notready": k_notready,
            "avg_utilization": (s_known / k_known) if k_known else -1.0,
        }
        return {"replicas": decision, "telemetry": telemetry}
