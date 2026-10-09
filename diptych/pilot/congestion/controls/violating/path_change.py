"""Violates robustness to a path change: never lets the window fall below 80%
of the largest window it has ever reached."""
from diptych.pilot.congestion.reference import Controller as _Reno


class Controller(_Reno):
    def __init__(self, config):
        super().__init__(config)
        self.peak = self.cwnd

    def tick(self, obs):
        d = super().tick(obs)
        self.peak = max(self.peak, self.cwnd)
        if not obs["timeout"]:
            self.cwnd = max(self.cwnd, 0.8 * self.peak)
            d = {"cwnd": self.cwnd, "telemetry": {"cwnd": self.cwnd, "ssthresh": self.ssthresh}}
        return d
