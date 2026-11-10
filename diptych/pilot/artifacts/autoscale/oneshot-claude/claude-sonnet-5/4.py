import math
from collections import deque
from statistics import fmean


class Controller:
    """Horizontal pod autoscaler for a single service."""

    def __init__(self, config: dict):
        self.min_replicas = int(config.get("min_replicas", 1))
        self.max_replicas = int(config.get("max_replicas", 60))
        self.initial_replicas = int(config.get("initial_replicas", 10))
        self.target_utilization = float(config.get("target_utilization", 0.6))
        self.tolerance = float(config.get("tolerance", 0.1))
        self.stabilization_ticks = int(config.get("stabilization_ticks", 20))
        # Bounded-memory state: only the last `stabilization_ticks` base
        # recommendations are kept (requirement 6).
        self._history = deque(maxlen=max(1, self.stabilization_ticks))

    def tick(self, obs: dict) -> dict:
        current = int(obs["replicas"])
        pods = obs.get("pods", [])

        ready_count = 0
        reported = []
        for pod in pods:
            if pod.get("ready"):
                ready_count += 1
                u = pod.get("utilization")
                if u is not None:
                    reported.append(u)

        # Pods still starting contribute nothing (equivalent to counting
        # them as ready-with-0%, which never enlarges the sum below) —
        # requirement 5.
        u_hat = fmean(reported) if reported else None

        if u_hat is None or ready_count == 0:
            # No usable signal this tick: hold steady rather than guess.
            raw_base = current
            ratio = 1.0
        else:
            # Ratio of measured average to target is all that matters
            # (requirement 7); missing metrics among ready pods are
            # estimated from the average of the pods that did report,
            # consistent with load being shared evenly (requirement 4).
            ratio = u_hat / self.target_utilization
            raw_base = math.ceil(ready_count * ratio)
            raw_base = max(self.min_replicas, min(self.max_replicas, raw_base))

        # This tick's base recommendation is included in its own window.
        self._history.append(raw_base)

        if abs(ratio - 1.0) <= self.tolerance:
            desired = current  # requirement 1: tolerance band
        else:
            desired = raw_base

        if desired > current:
            # requirement 2: bounded scale-up rate.
            candidate = min(desired, current + max(4, current))
        elif desired < current:
            # requirement 3: downscale stabilization window.
            floor = max(self._history)
            candidate = max(desired, floor)
            # Stabilization should only ever hold back a decrease, never
            # force an increase past the scale-up rate limit above.
            candidate = min(candidate, current)
        else:
            candidate = current

        candidate = max(self.min_replicas, min(self.max_replicas, int(candidate)))

        telemetry = {
            "ready_pods": ready_count,
            "reported_pods": len(reported),
            "avg_utilization": float(u_hat) if u_hat is not None else -1.0,
            "ratio": float(ratio),
            "raw_base_recommendation": int(raw_base),
            "stabilization_floor": int(max(self._history)),
            "desired_before_limits": int(desired),
            "current_replicas": current,
            "decision": int(candidate),
        }

        return {"replicas": int(candidate), "telemetry": telemetry}
