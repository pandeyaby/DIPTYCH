"""Reference "standard" algorithm: Reno-style AIMD (after RFC 5681), fluid form.

Slow start doubles the window per round trip until ``ssthresh``; congestion
avoidance adds one packet per round trip; a loss event halves the window at
most once per round trip; a timeout collapses it to one packet.
"""

from __future__ import annotations

import math


class Controller:
    DECREASE = 0.5
    INCREASE = 1.0          # packets per round trip in congestion avoidance
    TIMEOUT_CWND = 1.0
    REACT_TO_LOSS = True

    def __init__(self, config: dict):
        self.lo = float(config["min_cwnd"])
        self.hi = float(config["max_cwnd"])
        self.cwnd = float(config["initial_cwnd"])
        self.ssthresh = self.hi
        self.recover_until = -1

    def tick(self, obs: dict) -> dict:
        t, acked, lost = int(obs["t"]), float(obs["acked"]), float(obs["lost"])
        if obs["timeout"]:
            self.ssthresh = max(self.cwnd * self.DECREASE, 2.0)
            self.cwnd = self.TIMEOUT_CWND
            self.recover_until = t + math.ceil(float(obs["rtt"]))
        elif lost > 0 and self.REACT_TO_LOSS:
            if t >= self.recover_until:
                self.ssthresh = max(self.cwnd * self.DECREASE, 2.0)
                self.cwnd = self.ssthresh
                self.recover_until = t + math.ceil(float(obs["rtt"]))
        elif self.cwnd < self.ssthresh:
            self.cwnd += acked
        else:
            self.cwnd += self.INCREASE * acked / self.cwnd
        self.cwnd = min(self.hi, max(self.lo, self.cwnd))
        return {"cwnd": self.cwnd, "telemetry": {"cwnd": self.cwnd, "ssthresh": self.ssthresh}}
