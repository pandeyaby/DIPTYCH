"""Violates flapping resistance: scales on one pod's sample instead of the
average over pods."""
import math

from diptych.pilot.autoscale.controls.conforming import Controller as _HPA


class Controller(_HPA):
    def _recommend(self, obs):
        n = obs["replicas"]
        u = [p["utilization"] for p in obs["pods"] if p["ready"] and p["utilization"] is not None]
        if not u:
            return n
        ratio = u[0] / self.cfg["target_utilization"]
        if abs(ratio - 1.0) <= self.cfg["tolerance"]:
            return n
        return math.ceil(len(u) * ratio)
