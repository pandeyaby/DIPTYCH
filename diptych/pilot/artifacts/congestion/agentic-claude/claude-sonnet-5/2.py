"""Congestion control: delay-based (Vegas-style) congestion avoidance with a
loss-rate EWMA for sustained/high loss, periodic min-RTT probing, and a
timeout-driven full backoff.

Why hybrid: pure delay-based control cannot see loss that happens before the
bottleneck queue (e.g. a lossy link), so it never backs off from it -- good
for tolerating small random loss, bad for sustained high loss, which is a
real congestion/path signal and must still cut the window hard. The loss
EWMA supplies that second signal without reacting to the small amount of
background random loss present on every path.

The delay signal is a classic Vegas "diff": how many of this flow's own
packets are estimated to be sitting in the bottleneck queue right now,
``cwnd * (rtt - base_rtt) / rtt``. Keeping that in a band keeps the queue
this flow builds -- and thus the delay it imposes on everyone else -- small,
while still growing to use spare capacity.

``base_rtt`` is a lifetime minimum (so self-induced queueing can never make
it drift upward and mask real congestion), refreshed periodically by a short
deliberate probe that shrinks the window to drain the queue and remeasure --
otherwise a genuine path change (base RTT increasing) would be mistaken for
permanent self-congestion forever. Slow start exits as soon as the delay
signal shows *any* queueing, rather than waiting for a multi-packet
threshold: because queueing from this tick's offered load isn't visible
until next tick's RTT sample, slow start's doubling would otherwise overshoot
the bottleneck by several multiples before the reactive signal caught up.
"""

from __future__ import annotations

import math


class Controller:
    SS_EXIT_DIFF = 0.5       # packets: leave slow start at the first hint of queueing
    ALPHA = 0.8              # packets: steady-state band floor
    BETA = 1.8               # packets: steady-state band ceiling
    LOSS_EWMA_GAIN = 0.15
    LOSS_HIGH = 0.08         # loss rate treated as congestive, not noise
    LOSS_DECREASE_FLOOR = 0.5
    PROBE_INTERVAL = 250     # ticks between min-RTT probes
    PROBE_DURATION = 10      # ticks held at a small window to drain the queue
    PROBE_CWND = 2.0

    def __init__(self, config: dict):
        self.lo = float(config["min_cwnd"])
        self.hi = float(config["max_cwnd"])
        self.cwnd = float(config["initial_cwnd"])
        self.slow_start = True
        self.loss_ewma = 0.0
        self.recover_until = -1
        self.base_rtt = math.inf
        self.probing = False
        self.probe_end = -1
        self.probe_rtt_min = math.inf
        self.pre_probe_cwnd = self.cwnd
        self.next_probe_at = 150

    def tick(self, obs: dict) -> dict:
        t = int(obs["t"])
        acked = float(obs["acked"])
        lost = float(obs["lost"])
        rtt = float(obs["rtt"])
        sent = acked + lost

        if sent > 0:
            loss_frac = lost / sent
            self.loss_ewma += self.LOSS_EWMA_GAIN * (loss_frac - self.loss_ewma)

        if obs["timeout"]:
            phase = "timeout"
            self.cwnd = self.lo
            self.slow_start = False
            self.probing = False
            self.loss_ewma = max(self.loss_ewma, 0.5)
            self.recover_until = t + math.ceil(rtt)
            self.base_rtt = min(self.base_rtt, rtt)
        else:
            if not self.probing and t >= self.next_probe_at:
                self.probing = True
                self.probe_end = t + self.PROBE_DURATION
                self.probe_rtt_min = rtt
                self.pre_probe_cwnd = self.cwnd

            if self.probing:
                phase = "probe"
                self.probe_rtt_min = min(self.probe_rtt_min, rtt)
                self.cwnd = max(self.lo, self.PROBE_CWND)
                if t >= self.probe_end:
                    self.probing = False
                    self.base_rtt = self.probe_rtt_min
                    self.next_probe_at = t + self.PROBE_INTERVAL
                    self.cwnd = self.pre_probe_cwnd
            else:
                self.base_rtt = min(self.base_rtt, rtt)
                if self.loss_ewma > self.LOSS_HIGH and t >= self.recover_until:
                    phase = "loss_backoff"
                    factor = max(self.LOSS_DECREASE_FLOOR, 1.0 - self.loss_ewma)
                    self.cwnd *= factor
                    self.slow_start = False
                    self.recover_until = t + math.ceil(rtt)
                else:
                    diff = self.cwnd * (rtt - self.base_rtt) / rtt if rtt > 0 else 0.0
                    if self.slow_start and diff < self.SS_EXIT_DIFF:
                        phase = "slow_start"
                        self.cwnd += acked
                    else:
                        self.slow_start = False
                        step = acked / self.cwnd if self.cwnd > 0 else 0.0
                        mid = (self.ALPHA + self.BETA) / 2.0
                        if diff > self.BETA:
                            phase = "vegas_dec"
                            self.cwnd -= step
                        elif diff < self.ALPHA:
                            phase = "vegas_inc"
                            self.cwnd += step
                        elif diff > mid:
                            # Inside the band but above its midpoint: nudge gently
                            # back toward one target point instead of freezing,
                            # so the equilibrium queue doesn't depend on which
                            # value happened to land in the band first.
                            phase = "vegas_nudge_dec"
                            self.cwnd -= 0.3 * step
                        elif diff < mid:
                            phase = "vegas_nudge_inc"
                            self.cwnd += 0.3 * step
                        else:
                            phase = "vegas_hold"

        self.cwnd = min(self.hi, max(self.lo, self.cwnd))
        return {
            "cwnd": self.cwnd,
            "telemetry": {
                "cwnd": self.cwnd,
                "base_rtt": self.base_rtt if math.isfinite(self.base_rtt) else -1.0,
                "loss_ewma": self.loss_ewma,
                "phase": phase,
            },
        }
