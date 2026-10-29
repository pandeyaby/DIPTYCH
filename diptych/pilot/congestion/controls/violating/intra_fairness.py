"""Violates fairness within the algorithm: multiplicative increase (MIMD)
preserves the ratio between competing windows instead of equalizing them."""
from diptych.pilot.congestion.reference import Controller as _Reno


class Controller(_Reno):
    def tick(self, obs):
        before, ca = self.cwnd, self.cwnd >= self.ssthresh
        d = super().tick(obs)
        if ca and not obs["timeout"] and not obs["lost"]:
            self.cwnd = min(self.hi, before * (1.0 + 0.02 * float(obs["acked"]) / max(before, 1.0) * float(obs["rtt"])))
            d = {"cwnd": self.cwnd, "telemetry": {"cwnd": self.cwnd, "ssthresh": self.ssthresh}}
        return d
