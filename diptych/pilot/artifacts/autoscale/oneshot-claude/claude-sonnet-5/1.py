import math
from collections import deque


class Controller:
    def __init__(self, config: dict):
        self.min_replicas = config["min_replicas"]
        self.max_replicas = config["max_replicas"]
        self.initial_replicas = config.get("initial_replicas", self.min_replicas)
        self.target_utilization = config["target_utilization"]
        self.tolerance = config["tolerance"]
        self.stabilization_ticks = config["stabilization_ticks"]

        # Short bounded window used to damp per-pod measurement noise before
        # it feeds into a scaling decision.
        self._ratio_window = deque(maxlen=6)
        # Bounded history of base recommendations, kept slightly longer than
        # the stabilization window so decisions depend only on recent ticks.
        self._base_history = deque(maxlen=self.stabilization_ticks + 10)

    def tick(self, obs: dict) -> dict:
        pods = obs["pods"]
        current = obs["replicas"]

        known_ratios = []
        k_missing = 0
        k_starting = 0
        for pod in pods:
            if not pod["ready"]:
                k_starting += 1
            elif pod["utilization"] is None:
                k_missing += 1
            else:
                known_ratios.append(pod["utilization"] / self.target_utilization)

        k_known = len(known_ratios)
        instant_ratio = sum(known_ratios) / k_known if k_known else None

        if instant_ratio is not None:
            self._ratio_window.append(instant_ratio)

        smoothed_ratio = (
            sum(self._ratio_window) / len(self._ratio_window)
            if self._ratio_window
            else None
        )

        if smoothed_ratio is None or abs(smoothed_ratio - 1.0) <= self.tolerance:
            base = current
        else:
            # Ready pods with a metric scale by the measured ratio; pods whose
            # metric was withheld are assumed to sit exactly at target (so
            # missing data can only pull the decision toward the status quo);
            # pods still starting are assumed to be at 0% utilization (so they
            # can never make a scale-up larger than it would otherwise be).
            raw_sum = k_known * smoothed_ratio + k_missing * 1.0 + k_starting * 0.0
            base = max(1, math.ceil(raw_sum))

        base = min(max(base, self.min_replicas), self.max_replicas)
        self._base_history.append(base)

        recent = list(self._base_history)[-self.stabilization_ticks:]
        stabilized_floor = max(recent)

        rate_cap = current + max(4, current)
        decision = min(stabilized_floor, rate_cap)
        decision = min(max(decision, self.min_replicas), self.max_replicas)

        telemetry = {
            "t": obs.get("t", -1),
            "current_replicas": current,
            "ready_with_metric": k_known,
            "ready_missing_metric": k_missing,
            "starting_pods": k_starting,
            "instant_ratio": instant_ratio if instant_ratio is not None else -1.0,
            "smoothed_ratio": smoothed_ratio if smoothed_ratio is not None else -1.0,
            "base_recommendation": base,
            "stabilized_floor": stabilized_floor,
            "rate_cap": rate_cap,
            "decision": decision,
        }

        return {"replicas": decision, "telemetry": telemetry}
