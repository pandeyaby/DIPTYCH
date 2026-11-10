import math
from collections import deque


class Controller:
    def __init__(self, config: dict):
        self.min_replicas = config["min_replicas"]
        self.max_replicas = config["max_replicas"]
        self.target_utilization = config["target_utilization"]
        self.tolerance = config["tolerance"]
        self.stabilization_ticks = config["stabilization_ticks"]

        self.base_rec_history = deque(maxlen=max(1, self.stabilization_ticks))

    def _clamp(self, value: int) -> int:
        return max(self.min_replicas, min(self.max_replicas, value))

    def tick(self, obs: dict) -> dict:
        current = obs["replicas"]
        pods = obs["pods"]
        target = self.target_utilization

        ready_utils = [p["utilization"] for p in pods if p["ready"]]
        n_ready = len(ready_utils)
        known = [u for u in ready_utils if u is not None]
        k_known = len(known)
        missing_ready = n_ready - k_known

        have_data = k_known > 0

        if have_data:
            u_known_avg = sum(known) / k_known
            ratio_known = u_known_avg / target
            # Missing-metric ready pods only damp the decision: assume idle
            # when the known data says "scale up", assume at-target when it
            # says "scale down" (both scale correctly with target).
            fill = 0.0 if ratio_known >= 1.0 else target
            total_util = sum(known) + fill * missing_ready
            ratio = (total_util / n_ready) / target
            base_rec = math.ceil(total_util / target)
        else:
            ratio = 1.0
            base_rec = current

        base_rec = self._clamp(base_rec)
        self.base_rec_history.append(base_rec)

        if not have_data or abs(ratio - 1.0) <= self.tolerance:
            desired = current
        else:
            desired = base_rec

        if desired < current:
            floor_value = max(self.base_rec_history)
            if floor_value > desired:
                desired = floor_value

        if desired > current:
            max_up = current + max(4, current)
            if desired > max_up:
                desired = max_up

        desired = self._clamp(desired)

        telemetry = {
            "t": obs["t"],
            "replicas_before": current,
            "replicas_after": desired,
            "n_ready": n_ready,
            "k_known": k_known,
            "missing_ready": missing_ready,
            "utilization_ratio": ratio,
            "base_recommendation": base_rec,
            "have_data": str(have_data),
        }

        return {"replicas": desired, "telemetry": telemetry}
