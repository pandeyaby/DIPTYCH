"""A conservative, delay-aware AIMD congestion controller.

The controller uses delay as the primary indication of bottleneck congestion.
That makes isolated, non-congestive packet losses cheap, while retaining AIMD's
fairness and its coexistence properties.  A packet-weighted loss average is a
second, independent signal so that persistently lossy paths still cause full
rate reduction.
"""

from __future__ import annotations

import math


class Controller:
    """Delay-aware AIMD with random-loss tolerance."""

    def __init__(self, config: dict):
        self.lo = float(config["min_cwnd"])
        self.hi = float(config["max_cwnd"])
        self.cwnd = min(self.hi, max(self.lo, float(config["initial_cwnd"])))

        self.base_rtt = math.inf
        self.last_rtt = 0.0
        self.loss_ewma = 0.0
        self.loss_weight = 0.0
        self.recover_until = -1
        self.mode = "startup"
        self.elevated_ticks = 0
        self.elevated_min = math.inf

    def tick(self, obs: dict) -> dict:
        t = int(obs["t"])
        acked = max(0.0, float(obs["acked"]))
        lost = max(0.0, float(obs["lost"]))
        rtt = max(1.0e-9, float(obs["rtt"]))
        sent = acked + lost

        # A sudden delay step from an otherwise empty path is a route/base-RTT
        # change, rather than a queue that the flow built in a single tick.
        if not math.isfinite(self.base_rtt):
            self.base_rtt = rtt
        elif (lost == 0.0 and self.last_rtt > 0.0
              and self.last_rtt <= 1.35 * self.base_rtt
              and rtt >= 1.45 * self.last_rtt):
            self.base_rtt = rtt
        else:
            self.base_rtt = min(self.base_rtt, rtt)

        # Also handle a less abrupt route change.  A real standing queue at
        # this height eventually overflows and resets this counter; an empty
        # path with a new propagation delay does not.
        if rtt > 1.45 * self.base_rtt and lost == 0.0:
            self.elevated_ticks += 1
            self.elevated_min = min(self.elevated_min, rtt)
            if self.elevated_ticks >= max(12, math.ceil(6.0 * rtt)):
                self.base_rtt = self.elevated_min
                self.elevated_ticks = 0
                self.elevated_min = math.inf
        else:
            self.elevated_ticks = 0
            self.elevated_min = math.inf

        # Packet-weighted EWMA.  About five RTTs of sustained loss dominate
        # the average, whereas a one-packet random event quickly disappears.
        sample_loss = lost / sent if sent > 0.0 else 0.0
        alpha = min(0.35, sent / max(1.0, 5.0 * self.cwnd))
        self.loss_ewma += alpha * (sample_loss - self.loss_ewma)
        self.loss_weight = min(1.0, self.loss_weight + alpha)

        delay_ratio = rtt / self.base_rtt
        congested = delay_ratio > 1.18
        high_loss = self.loss_weight >= 0.35 and self.loss_ewma >= 0.10

        if bool(obs["timeout"]):
            self.cwnd = self.lo
            self.recover_until = t + max(1, math.ceil(rtt))
            self.mode = "timeout"
        elif t >= self.recover_until and (congested or high_loss):
            # Strong loss is treated like Reno.  Delay-only backoff is gentler
            # and therefore holds a small standing queue without oscillating.
            factor = 0.5 if (high_loss or lost > 0.0) else 0.85
            self.cwnd *= factor
            self.recover_until = t + max(1, math.ceil(rtt))
            self.mode = "backoff"
        else:
            if self.mode == "startup" and delay_ratio <= 1.08 and lost == 0.0:
                # Fluid equivalent of slow start, limited to one window per RTT.
                self.cwnd += acked
            else:
                self.mode = "avoidance"
                # One packet per RTT (Reno's additive increase).  Do not let a
                # random loss suppress probing for available capacity.
                self.cwnd += acked / max(self.cwnd, 1.0)

        self.cwnd = min(self.hi, max(self.lo, self.cwnd))
        self.last_rtt = rtt
        telemetry = {
            "cwnd": self.cwnd,
            "base_rtt": self.base_rtt,
            "delay_ratio": delay_ratio,
            "loss_ewma": self.loss_ewma,
            "mode": self.mode,
        }
        return {"cwnd": self.cwnd, "telemetry": telemetry}
