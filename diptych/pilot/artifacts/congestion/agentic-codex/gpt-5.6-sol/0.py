"""A conservative delay-based congestion controller.

The controller is intentionally friendly to loss-based TCP: it aims for a
small amount of queued data, and regards a full queue (rather than every
individual loss) as congestion.  A filtered loss fraction provides the
separate safety response required for persistently lossy paths.
"""

from __future__ import annotations

import math


class Controller:
    """Window controller with Vegas-like delay control and loss safeguards."""

    LOSS_GAIN = 0.08
    HIGH_LOSS = 0.08
    TARGET_LOW = 1.75
    TARGET_HIGH = 3.75

    def __init__(self, config: dict):
        self.lo = float(config["min_cwnd"])
        self.hi = float(config["max_cwnd"])
        self.cwnd = min(self.hi, max(self.lo, float(config["initial_cwnd"])))

        self.base_rtt: float | None = None
        self.last_rtt: float | None = None
        self.rebase_samples: list[float] = []
        self.loss_ewma = 0.0
        self.slow_start = True
        self.recover_until = -1

    def _clamp(self) -> None:
        if not math.isfinite(self.cwnd):
            self.cwnd = self.lo
        self.cwnd = min(self.hi, max(self.lo, self.cwnd))

    def tick(self, obs: dict) -> dict:
        t = int(obs["t"])
        acked = max(0.0, float(obs["acked"]))
        lost = max(0.0, float(obs["lost"]))
        rtt = max(1.0e-9, float(obs["rtt"]))

        if self.base_rtt is None or rtt < self.base_rtt:
            self.base_rtt = rtt
        # Do not simply use a rolling minimum: a standing queue would then be
        # mistaken for propagation delay and the window would slowly inflate.
        # An upward rebase starts only after a discontinuous RTT jump, then
        # requires that new floor to persist.  Queue growth is continuous in
        # this model, whereas a changed propagation path is a step.
        if (self.last_rtt is not None and not self.rebase_samples
                and rtt > 1.5 * self.last_rtt
                and rtt > 1.5 * self.base_rtt):
            self.rebase_samples = [rtt]
        elif self.rebase_samples:
            if rtt > 1.5 * self.base_rtt:
                self.rebase_samples.append(rtt)
                if len(self.rebase_samples) >= 16:
                    self.base_rtt = min(self.rebase_samples)
                    self.rebase_samples = []
            else:
                self.rebase_samples = []
        self.last_rtt = rtt
        base_rtt = self.base_rtt
        # Number of this flow's packets attributable to queueing (the usual
        # Vegas diff), independent of the path's unknown capacity and buffer.
        queued = self.cwnd * max(0.0, rtt - base_rtt) / rtt

        sent = acked + lost
        sample_loss = lost / sent if sent > 0.0 else 0.0
        self.loss_ewma += self.LOSS_GAIN * (sample_loss - self.loss_ewma)

        if bool(obs["timeout"]):
            # Full backoff is immediate, including during a total blackout.
            self.cwnd = min(2.0, max(self.lo, 1.0))
            self.slow_start = False
            self.recover_until = t + max(1, math.ceil(rtt))

        elif self.loss_ewma >= self.HIGH_LOSS:
            # Persistent high loss must reduce the offered rate even when it
            # occurs before the bottleneck and therefore creates no queue.
            if t >= self.recover_until:
                severity = min(1.0, self.loss_ewma)
                self.cwnd *= max(0.50, 1.0 - 1.5 * severity)
                self.recover_until = t + max(1, math.ceil(rtt))
            self.slow_start = False

        elif lost > 0.0 and queued > self.TARGET_HIGH:
            # Loss accompanied by excess delay is congestion.  Rate-limit the
            # response to once per RTT, as Reno does for one loss episode.
            if t >= self.recover_until:
                self.cwnd *= 0.50
                self.recover_until = t + max(1, math.ceil(rtt))
            self.slow_start = False

        elif self.slow_start:
            if queued >= self.TARGET_HIGH:
                self.slow_start = False
                self.cwnd -= acked / max(self.cwnd, 1.0)
            else:
                # Increase by one window per RTT.
                self.cwnd += acked

        else:
            # Add or remove one packet per RTT.  The dead band prevents noise
            # around the small target queue from causing needless oscillation.
            step = acked / max(self.cwnd, 1.0)
            if queued < self.TARGET_LOW:
                self.cwnd += step
            elif queued > self.TARGET_HIGH:
                self.cwnd -= step

        self._clamp()
        mode = "startup" if self.slow_start else "delay"
        if self.loss_ewma >= self.HIGH_LOSS:
            mode = "high_loss"
        if bool(obs["timeout"]):
            mode = "timeout"
        return {
            "cwnd": float(self.cwnd),
            "telemetry": {
                "cwnd": float(self.cwnd),
                "base_rtt": float(base_rtt),
                "queued": float(queued),
                "loss_ewma": float(self.loss_ewma),
                "mode": mode,
            },
        }
