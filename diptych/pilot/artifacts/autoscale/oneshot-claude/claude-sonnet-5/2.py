import math
from collections import deque


class Controller:
    """Horizontal pod autoscaler for a single service."""

    def __init__(self, config: dict):
        self.min_replicas = int(config.get("min_replicas", 1))
        self.max_replicas = int(config.get("max_replicas", 60))
        self.initial_replicas = int(config.get("initial_replicas", 10))
        self.target_utilization = float(config.get("target_utilization", 0.6))
        self.tolerance = float(config.get("tolerance", 0.1))
        self.stabilization_ticks = int(config.get("stabilization_ticks", 20))
        self._history = deque(maxlen=max(1, self.stabilization_ticks))

    def _clip(self, n: int) -> int:
        return max(self.min_replicas, min(self.max_replicas, n))

    def _recommend(self, k: int, sum_u: float, n: int) -> int:
        """ceil(k * avg / target), or `n` if there's no data or it's within tolerance."""
        if k <= 0:
            return n
        ratio = (sum_u / k) / self.target_utilization
        if abs(ratio - 1.0) <= self.tolerance:
            return n
        return int(math.ceil(sum_u / self.target_utilization))

    def tick(self, obs: dict) -> dict:
        t = obs.get("t", 0)
        n = int(obs.get("replicas", self.initial_replicas))
        pods = obs.get("pods", [])

        k = 0        # ready pods with a usable metric
        sum_u = 0.0  # sum of their utilization
        m = 0        # ready pods whose metric is missing
        r = 0        # not-yet-ready (starting) pods

        for p in pods:
            if not p.get("ready"):
                r += 1
                continue
            u = p.get("utilization")
            if u is None:
                m += 1
            else:
                k += 1
                sum_u += u

        base_rec = self._recommend(k, sum_u, n)
        self._history.append(base_rec)

        target = self.target_utilization
        ratio = (sum_u / k) / target if k > 0 else 1.0

        if ratio > 1.0 + self.tolerance:
            # Scale-up signal: missing-metric pods are treated as consuming
            # 0% (they already carry no weight in `base_rec`), so no change.
            desired = base_rec
        elif ratio < 1.0 - self.tolerance:
            # Scale-down signal: missing-metric pods are treated as
            # consuming exactly the target, each adding back one replica,
            # never pushing the result past the current count.
            desired = min(base_rec + m, n)
        else:
            desired = n

        # Starting pods must not justify a bigger scale-up than if they
        # were already ready and reporting 0% utilization.
        cap_rec = self._recommend(k + r, sum_u, n)
        desired = min(desired, max(cap_rec, n))

        if desired > n:
            desired = min(desired, n + max(4, n))
        elif desired < n:
            desired = max(desired, max(self._history))

        final = self._clip(int(desired))

        if final > n:
            reason = "scale_up"
        elif final < n:
            reason = "scale_down"
        else:
            reason = "steady"

        telemetry = {
            "t": float(t),
            "replicas_before": float(n),
            "ready_with_metric": float(k),
            "missing_metric": float(m),
            "starting": float(r),
            "avg_utilization": float(sum_u / k) if k > 0 else 0.0,
            "utilization_ratio": float(ratio),
            "base_recommendation": float(base_rec),
            "stabilization_floor": float(max(self._history)),
            "final_replicas": float(final),
            "reason": reason,
        }

        return {"replicas": final, "telemetry": telemetry}
