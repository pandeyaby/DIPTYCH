import math
from collections import deque


class Controller:
    """Horizontal pod autoscaler for a single service."""

    # Number of recent ticks' utilization ratios averaged together to
    # damp per-tick measurement noise. Kept small and fixed so decisions
    # only ever depend on a short, bounded window of history.
    _SMOOTHING_WINDOW = 4

    def __init__(self, config: dict):
        self.min_replicas = config["min_replicas"]
        self.max_replicas = config["max_replicas"]
        self.initial_replicas = config["initial_replicas"]
        self.target_utilization = config["target_utilization"]
        self.tolerance = config["tolerance"]
        self.stabilization_ticks = config["stabilization_ticks"]

        self._ratio_window = deque(maxlen=self._SMOOTHING_WINDOW)
        self._raw_rec_history = deque(maxlen=self.stabilization_ticks)

    def tick(self, obs: dict) -> dict:
        pods = obs["pods"]
        current = obs["replicas"]
        target = self.target_utilization
        tol = self.tolerance

        metrics = [p["utilization"] for p in pods if p["ready"] and p["utilization"] is not None]
        k1 = len(metrics)
        missing_ready = sum(1 for p in pods if p["ready"] and p["utilization"] is None)
        not_ready = sum(1 for p in pods if not p["ready"])

        ratio1 = None
        if k1 > 0:
            ratio1 = (sum(metrics) / k1) / target
            self._ratio_window.append(ratio1)

        smoothed_ratio = None
        if self._ratio_window:
            smoothed_ratio = sum(self._ratio_window) / len(self._ratio_window)

        if smoothed_ratio is None or k1 == 0:
            raw_rec = current
            damped_rec = current
            aug_ratio = smoothed_ratio if smoothed_ratio is not None else 1.0
        else:
            eff_sum1 = smoothed_ratio * target * k1

            if abs(smoothed_ratio - 1.0) <= tol:
                raw_rec = current
            else:
                raw_rec = math.ceil(eff_sum1 / target)

            if smoothed_ratio > 1.0:
                # Scaling up: pods still starting or missing a metric are
                # conservatively assumed to be at 0% utilization, which can
                # only shrink (never enlarge) the scale-up.
                aug_k = k1 + missing_ready + not_ready
                aug_sum = eff_sum1
            else:
                # Scaling down (or flat): a missing metric is conservatively
                # assumed to be exactly at the target, which can only pull
                # the decision back toward "no change". Not-ready pods are
                # left out entirely, since there is no reason to believe
                # they warrant a deeper cut.
                aug_k = k1 + missing_ready
                aug_sum = eff_sum1 + missing_ready * target

            aug_ratio = (aug_sum / aug_k) / target if aug_k > 0 else smoothed_ratio
            reversed_sign = (aug_ratio > 1.0) != (smoothed_ratio > 1.0)

            if reversed_sign or abs(aug_ratio - 1.0) <= tol:
                damped_rec = current
            else:
                damped_rec = math.ceil(aug_sum / target)

        self._raw_rec_history.append(raw_rec)
        floor = max(self._raw_rec_history)

        decision = damped_rec
        if decision < current:
            decision = max(decision, floor)
            decision = min(decision, current)
        elif decision > current:
            cap = current + max(4, current)
            decision = min(decision, cap)

        decision = max(self.min_replicas, min(self.max_replicas, decision))

        telemetry = {
            "current_replicas": current,
            "k_ready_with_metric": k1,
            "missing_ready": missing_ready,
            "not_ready": not_ready,
            "smoothed_ratio": float(smoothed_ratio) if smoothed_ratio is not None else 0.0,
            "aug_ratio": float(aug_ratio) if aug_ratio is not None else 0.0,
            "raw_rec": raw_rec,
            "damped_rec": damped_rec,
            "stabilization_floor": floor,
            "final_replicas": decision,
        }

        return {"replicas": decision, "telemetry": telemetry}
