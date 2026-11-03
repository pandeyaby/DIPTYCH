"""Violates missing-metrics conservatism: a pod with no metric is assumed
saturated, so missing metrics enlarge a scale-up."""
import math

from diptych.pilot.autoscale.controls.conforming import Controller as _HPA

SATURATED = 1.0


class Controller(_HPA):
    def _recommend(self, obs):
        n = obs["replicas"]
        u = [SATURATED if p["utilization"] is None else p["utilization"] for p in obs["pods"] if p["ready"]]
        if not u:
            return n
        ratio = (sum(u) / len(u)) / self.cfg["target_utilization"]
        if abs(ratio - 1.0) <= self.cfg["tolerance"]:
            return n
        return math.ceil(len(u) * ratio)
