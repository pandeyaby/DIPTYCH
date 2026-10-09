"""Violates robustness to a delay change: treats any RTT above 1.5x the
smallest RTT ever seen as congestion, and never revises that minimum."""
from diptych.pilot.congestion.reference import Controller as _Reno


class Controller(_Reno):
    def __init__(self, config):
        super().__init__(config)
        self.min_rtt = None

    def tick(self, obs):
        rtt = float(obs["rtt"])
        self.min_rtt = rtt if self.min_rtt is None else min(self.min_rtt, rtt)
        d = super().tick(obs)
        if rtt > 1.5 * self.min_rtt and not obs["timeout"]:
            self.cwnd = max(self.lo, self.cwnd * 0.9)
            d = {"cwnd": self.cwnd, "telemetry": {"cwnd": self.cwnd, "ssthresh": self.ssthresh}}
        return d
