"""Horizontal pod autoscaler controller.

Implements the Kubernetes HPA algorithm (see "Horizontal Pod Autoscaling",
kubernetes.io, "Algorithm details" and "Default behavior" sections):

  * base recommendation = ceil(k * u / target), held at the current replica
    count whenever u / target is within `tolerance` of 1 ("Tolerance");
  * missing metrics on ready pods damp rather than amplify a decision: for a
    scale-up they are (implicitly, via the sum-based formula below) treated
    as reporting 0% utilization; for a scale-down they are treated as
    reporting exactly the target utilization, which pulls the recommendation
    back toward the current count ("Algorithm details");
  * not-yet-ready pods are simply excluded from the formula, which has the
    same effect as counting them as ready at 0% utilization -- they can
    never inflate a scale-up ("Algorithm details");
  * scale-ups are rate-limited to `max(4, current)` additional pods per tick
    ("Default behavior");
  * scale-downs are floored by the highest base recommendation seen in the
    last `stabilization_ticks` ticks ("Stabilization window").
"""

from __future__ import annotations

import math
from collections import deque
from typing import Any


class Controller:
    def __init__(self, config: dict):
        self.min_replicas = int(config["min_replicas"])
        self.max_replicas = int(config["max_replicas"])
        self.target = float(config["target_utilization"])
        self.tolerance = float(config["tolerance"])
        self.window = int(config["stabilization_ticks"])
        self.history: deque[int] = deque(maxlen=max(1, self.window))

    def _recommend(self, total_usage: float, count: int, current: int) -> int:
        """ceil(count * avg_u / target), held at `current` within tolerance."""
        if count <= 0:
            return current
        ratio = (total_usage / count) / self.target
        if abs(ratio - 1.0) <= self.tolerance:
            return current
        return math.ceil(total_usage / self.target)

    def tick(self, obs: dict) -> dict:
        current = int(obs["replicas"])
        pods = obs["pods"]

        ready = [p for p in pods if p["ready"]]
        reporting = [p["utilization"] for p in ready if p["utilization"] is not None]
        k_r = len(reporting)
        m = len(ready) - k_r  # ready pods with a withheld metric
        usage_r = sum(reporting)

        rec = self._recommend(usage_r, k_r, current)

        if rec < current and m > 0:
            # Conservatively assume withheld metrics read exactly the
            # target, which only pulls the recommendation up (toward
            # `current`), never past it and never below the unadjusted one.
            adj_usage = usage_r + m * self.target
            adj_count = k_r + m
            rec2 = self._recommend(adj_usage, adj_count, current)
            if rec2 > rec:
                rec = rec2

        self.history.append(rec)

        if rec > current:
            desired = min(rec, current + max(4, current))
        elif rec < current:
            desired = max(rec, max(self.history))
        else:
            desired = current

        desired = max(self.min_replicas, min(self.max_replicas, int(desired)))

        telemetry: dict[str, Any] = {
            "ready": len(ready),
            "starting": len(pods) - len(ready),
            "reporting": k_r,
            "missing": m,
            "avg_utilization": (usage_r / k_r) if k_r else -1.0,
            "base_rec": rec,
            "window_max": max(self.history),
            "current": current,
        }
        return {"replicas": desired, "telemetry": telemetry}
