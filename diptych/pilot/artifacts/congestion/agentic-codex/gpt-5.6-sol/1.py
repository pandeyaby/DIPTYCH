"""A conservative, delay-sensitive congestion controller.

The controller uses RTT inflation as its primary congestion signal.  Packet
loss is also used, but only after its *rate* has been smoothed; this avoids the
large throughput penalty Reno pays for occasional, non-congestive losses.
"""

from __future__ import annotations

import math


class Controller:
    """Delay AIMD with loss-rate and timeout safety valves."""

    def __init__(self, config: dict):
        self.lo = float(config["min_cwnd"])
        self.hi = float(config["max_cwnd"])
        self.cwnd = min(self.hi, max(self.lo, float(config["initial_cwnd"])))

        self.base_rtt: float | None = None
        self.last_rtt: float | None = None
        self.stable_ticks = 0
        self.loss_ewma = 0.0
        self.recover_until = -1
        self.started = False
        self.first_t: int | None = None
        self.probe_at: int | None = None
        self.probing_until = -1
        self.probed = False

    def _update_base_rtt(self, rtt: float) -> None:
        """Track propagation delay, including upward route changes.

        A lifetime minimum alone cannot recover when a route's propagation
        delay increases.  A guarded step detector handles that case without
        allowing an ordinary, gradually varying queue into the baseline.
        """
        if self.base_rtt is None:
            self.base_rtt = rtt
            self.last_rtt = rtt
            return

        if rtt < self.base_rtt:
            self.base_rtt = rtt
        elif (self.last_rtt is not None
              and self.stable_ticks >= 16
              and self.last_rtt < 1.45 * self.base_rtt
              and rtt > 1.45 * self.last_rtt):
            # A propagation-delay change is an instantaneous vertical step.
            # Queue growth is normally gradual in this model.  Preserve the
            # old queue estimate and attribute only the step to propagation.
            self.base_rtt += rtt - self.last_rtt
        if self.last_rtt is not None and abs(rtt - self.last_rtt) <= 0.03 * self.last_rtt:
            self.stable_ticks += 1
        else:
            self.stable_ticks = 0
        self.last_rtt = rtt

    def tick(self, obs: dict) -> dict:
        t = int(obs["t"])
        acked = max(0.0, float(obs["acked"]))
        lost = max(0.0, float(obs["lost"]))
        rtt = max(1.0e-9, float(obs["rtt"]))
        timeout = bool(obs["timeout"])

        if self.first_t is None:
            self.first_t = t
            # All instances use the same coarse epoch.  Thus established and
            # late-starting instances probe together instead of alternately
            # forcing one another out of the queue.
            self.probe_at = ((t // 200) + 1) * 200

        self._update_base_rtt(rtt)
        base = self.base_rtt if self.base_rtt is not None else rtt
        qratio = max(0.0, (rtt - base) / base)

        sent = acked + lost
        loss_sample = lost / sent if sent > 0.0 else 0.0
        # About one RTT of memory, with a lower bound to keep this stable on
        # very short paths.  Sustained loss is retained; an isolated one-packet
        # random loss quickly washes out.
        alpha = min(0.25, max(0.04, 1.0 / max(4.0, rtt)))
        self.loss_ewma += alpha * (loss_sample - self.loss_ewma)

        probing = False
        if (not self.probed and self.probe_at is not None
                and t >= self.probe_at and t - self.first_t >= 30):
            if self.probing_until < 0:
                self.probing_until = t + 7
            probing = t <= self.probing_until
            if probing:
                self.cwnd = self.lo
            else:
                self.probed = True
                self.cwnd = min(self.hi, max(self.lo, 4.0))
                self.started = False

        if probing:
            pass
        elif timeout:
            # Full backoff is deliberately unconditional: if no traffic gets
            # through, delay and probabilistic classifiers cannot help.
            self.cwnd = min(2.0, max(self.lo, 1.0))
            self.recover_until = t + int(math.ceil(rtt))
        elif self.loss_ewma >= 0.12:
            # Sustained 20% loss must reduce the offered rate substantially.
            # Rate-limit multiplicative reactions to approximately once/RTT.
            if t >= self.recover_until:
                factor = 0.70 if self.loss_ewma < 0.20 else 0.60
                self.cwnd *= factor
                self.recover_until = t + int(math.ceil(rtt))
        elif qratio > 0.32:
            # Back off before a drop-tail queue becomes full.  The reduction
            # scales gently with excess delay and occurs at most once per RTT.
            if t >= self.recover_until:
                factor = max(0.70, 0.88 - 0.12 * min(qratio, 1.5))
                self.cwnd *= factor
                self.recover_until = t + int(math.ceil(rtt))
            self.started = True
        elif qratio < 0.16:
            if not self.started and self.loss_ewma < 0.03:
                # Bounded slow start.  acked/cwnd is approximately 1/RTT, so
                # this is Reno slow start expressed in the tick interface.
                self.cwnd += acked
            else:
                # One packet per RTT, Reno's congestion-avoidance increase.
                self.cwnd += acked / max(self.cwnd, 1.0)
        else:
            # Inside the delay target band, use a small additive probe so the
            # controller follows capacity changes without creating a standing
            # full queue.
            self.started = True
            self.cwnd += 0.20 * acked / max(self.cwnd, 1.0)

        self.cwnd = min(self.hi, max(self.lo, self.cwnd))
        telemetry = {
            "cwnd": self.cwnd,
            "base_rtt": base,
            "queue_ratio": qratio,
            "loss_ewma": self.loss_ewma,
            "mode": ("probe_rtt" if probing else "timeout" if timeout
                     else "loss" if self.loss_ewma >= 0.12
                     else "delay" if qratio > 0.32 else "probe"),
        }
        return {"cwnd": self.cwnd, "telemetry": telemetry}
