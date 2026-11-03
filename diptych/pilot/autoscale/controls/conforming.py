"""Conforming control: the Kubernetes HPA algorithm as documented.

Base ratio over ready pods with metrics; tolerance; conservative recompute
for missing metrics (100% of target on scale-down, 0% on scale-up) and for
not-yet-ready pods on scale-up (0%); no action if the recompute reverses
direction or falls inside the tolerance; downscale stabilization (highest
recommendation in the window); scale-up limited to max(+4, +100%) per tick.
"""

from __future__ import annotations

import math


class Controller:
    def __init__(self, config: dict):
        self.cfg = dict(config)
        self.recs: list[tuple[int, int]] = []   # (tick, recommendation)

    def _recommend(self, obs: dict) -> int:
        n = obs["replicas"]
        target = self.cfg["target_utilization"]
        tol = self.cfg["tolerance"]
        pods = obs["pods"]
        reported = [p["utilization"] for p in pods if p["ready"] and p["utilization"] is not None]
        missing = sum(1 for p in pods if p["ready"] and p["utilization"] is None)
        unready = sum(1 for p in pods if not p["ready"])
        if not reported:
            return n
        ratio = (sum(reported) / len(reported)) / target
        if abs(ratio - 1.0) <= tol:
            return n
        if missing == 0 and unready == 0:
            return math.ceil(len(reported) * ratio)
        up = ratio > 1.0
        total = sum(reported)
        count = len(reported)
        if missing:
            total += missing * (0.0 if up else target)
            count += missing
        if up and unready:
            count += unready          # not-yet-ready pods count as 0%
        new_ratio = (total / count) / target
        if (up and new_ratio <= 1.0) or (not up and new_ratio >= 1.0) or abs(new_ratio - 1.0) <= tol:
            return n
        return math.ceil(count * new_ratio)

    def _stabilize(self, n: int, rec: int, t: int) -> int:
        """Scale-down uses the highest recommendation in the window."""
        window = int(self.cfg["stabilization_ticks"])
        self.recs.append((t, rec))
        self.recs = [(k, r) for k, r in self.recs if k > t - window]
        return min(n, max(r for _, r in self.recs)) if rec < n else rec

    def _limit(self, n: int, desired: int) -> int:
        """Scale-up adds at most max(4 pods, 100%) per tick."""
        return min(desired, max(2 * n, n + 4)) if desired > n else desired

    def tick(self, obs: dict) -> dict:
        n = obs["replicas"]
        rec = self._recommend(obs)
        desired = self._limit(n, self._stabilize(n, rec, obs["t"]))
        desired = int(min(self.cfg["max_replicas"], max(self.cfg["min_replicas"], desired)))
        return {"replicas": desired, "telemetry": {"recommendation": rec, "replicas": desired}}
