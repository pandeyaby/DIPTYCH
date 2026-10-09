"""A conservative, delay-guided congestion controller.

The controller uses queuing delay as its primary congestion signal.  This
lets it avoid treating an isolated (and usually non-congestive) packet loss
like Reno does, while remaining deliberately polite to loss-based traffic.
"""

from __future__ import annotations

import math


class Controller:
    """Delay-targeted AIMD with sustained-loss and timeout safeguards."""

    def __init__(self, config: dict):
        self.lo = float(config["min_cwnd"])
        self.hi = float(config["max_cwnd"])
        self.cwnd = min(self.hi, max(self.lo, float(config["initial_cwnd"])))

        self.base_rtt = math.inf
        self.prev_rtt = 0.0
        self.slow_start = True

        # Loss is filtered over several ticks.  A lone random loss therefore
        # has little effect, whereas a persistent lossy path is recognized
        # quickly and causes repeated reduction.
        self.loss_ema = 0.0
        self.loss_ticks = 0
        self.recover_until = -1

    def _clamp(self, value: float) -> float:
        if not math.isfinite(value):
            return self.lo
        return min(self.hi, max(self.lo, value))

    def tick(self, obs: dict) -> dict:
        t = int(obs["t"])
        acked = max(0.0, float(obs["acked"]))
        lost = max(0.0, float(obs["lost"]))
        rtt = max(1.0e-9, float(obs["rtt"]))
        total = acked + lost
        fraction_lost = lost / total if total > 0.0 else 0.0

        # A discontinuous RTT increase is characteristic of a route/base-RTT
        # change in this model.  Preserve approximately the same sending rate
        # by scaling the window.  Queue growth is gradual and does not trip
        # this guard.
        path_shift = self.prev_rtt > 0.0 and rtt > self.prev_rtt * 1.55
        if path_shift and fraction_lost < 0.05 and not bool(obs["timeout"]):
            old = self.prev_rtt
            self.cwnd *= rtt / old
            self.base_rtt = rtt
        self.prev_rtt = rtt

        if not math.isfinite(self.base_rtt) or rtt < self.base_rtt:
            self.base_rtt = rtt

        if fraction_lost >= 0.10:
            self.loss_ticks += 1
        else:
            self.loss_ticks = 0
        self.loss_ema = 0.75 * self.loss_ema + 0.25 * fraction_lost

        if bool(obs["timeout"]):
            self.cwnd = min(2.0, max(self.lo, 1.0))
            self.slow_start = False
            self.loss_ema = max(self.loss_ema, 0.5)
            self.recover_until = t + max(1, int(math.ceil(rtt)))
            mode = "timeout"
        else:
            qratio = max(0.0, (rtt - self.base_rtt) / self.base_rtt)

            # Loss at a clearly inflated RTT is overflow, rather than random
            # corruption.  Match Reno's response so a loss-based competitor
            # is not disadvantaged.  At low delay, isolated loss is filtered.
            if lost > 0.0 and qratio >= 0.35 and t >= self.recover_until:
                self.cwnd *= 0.5
                self.recover_until = t + max(1, int(math.ceil(rtt)))
                self.slow_start = False
                mode = "congestion_backoff"

            # Sustained high loss is handled separately from occasional loss.
            # Once recognized, reduce at most once per RTT, by an amount tied
            # to the observed loss rate.
            elif self.loss_ticks >= 3 and t >= self.recover_until:
                factor = max(0.50, 1.0 - 1.5 * self.loss_ema)
                self.cwnd *= factor
                self.recover_until = t + max(1, int(math.ceil(rtt)))
                self.slow_start = False
                mode = "loss_backoff"
            elif self.slow_start:
                if qratio >= 0.10 or fraction_lost >= 0.10:
                    self.slow_start = False
                    mode = "avoidance"
                else:
                    self.cwnd += acked
                    mode = "startup"
            else:
                # One packet per RTT is Reno's additive increase.  Above the
                # delay target, a window-proportional term smoothly removes
                # excess load; this converges equal controllers toward equal
                # shares and yields early to a queue-filling Reno flow.
                increase = acked / max(self.cwnd, 1.0)
                excess = max(0.0, qratio - 0.20)
                decrease = 0.55 * excess * acked
                self.cwnd += increase - decrease
                mode = "delay_backoff" if decrease > increase else "avoidance"

            # Delay controllers otherwise suffer from latecomer advantage: a
            # flow starting behind an existing queue records that queue as
            # propagation delay.  All instances use the public tick index to
            # make a brief, infrequent drain probe.  The resulting low-rate
            # interval refreshes their minima and also gives short competing
            # flows a regular opening.
            if t > 0 and t % 100 == 0:
                self.cwnd *= 0.5
                mode = "drain_probe"

        self.cwnd = self._clamp(self.cwnd)
        telemetry = {
            "cwnd": self.cwnd,
            "base_rtt": self.base_rtt,
            "loss_ema": self.loss_ema,
            "mode": mode,
        }
        return {"cwnd": self.cwnd, "telemetry": telemetry}
