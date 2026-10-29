"""Congestion control algorithm: delay-based (FAST-style) control with an
explicit loss-rate backstop and timeout collapse.

Primary signal is queueing delay, estimated as
``diff = cwnd * (rtt - base_rtt) / rtt`` (packets this flow has queued, by
comparing the rate it is achieving to the rate it would achieve at the path's
minimum RTT). In congestion avoidance the window is driven towards the level
that keeps ``diff`` near a small constant ``alpha`` (packets), via damped
proportional control (as in FAST TCP). This makes the controller largely
insensitive to incidental packet loss (it does not react to loss unless the
loss rate is sustained and high), keeps the self-inflicted queue small, and
converges to a fair, capacity-tracking allocation among flows that share a
bottleneck, since it responds to the directly observed queueing delay rather
than to a fixed increase/decrease schedule.

A separate EWMA of the per-tick loss fraction provides a backstop: when
sustained loss is high (well above the rate caused by rare incidental drops),
the window is multiplicatively cut, at most once per round trip, like a loss
event in AIMD. A timeout (nothing acknowledged) collapses the window to the
minimum, as standard congestion control does.

The minimum RTT used as the delay baseline is tracked over a sliding window
(two half-windows) rather than as a lifetime minimum, so the controller can
adapt if the path's true minimum delay increases.
"""

from __future__ import annotations


class Controller:
    MIN_RTT_WINDOW = 80.0     # ticks; how fast the base-RTT estimate can rise
    ALPHA_PKTS = 0.8          # target self-inflicted queue, in packets
    GAIN = 1.0                # damping factor for the proportional update
    SS_EXIT_DIFF = 0.8        # exit slow start once estimated queueing reaches this
    LOSS_EWMA_ALPHA = 0.05    # smoothing for the loss-rate estimate
    LOSS_DECREASE_THRESH = 0.10  # loss-rate EWMA above this triggers a cut
    LOSS_DECREASE_FLOOR = 0.3    # worst-case per-event multiplicative cut

    def __init__(self, config: dict):
        self.lo = float(config["min_cwnd"])
        self.hi = float(config["max_cwnd"])
        self.cwnd = float(config["initial_cwnd"])
        self.mode = "ss"
        self.base_rtt_est: float | None = None
        self._cur_win_min: float | None = None
        self._win_start = 0
        self.loss_ewma = 0.0
        self.recover_until = -1

    def _update_base_rtt(self, t: int, rtt: float) -> None:
        if self._cur_win_min is None:
            self._cur_win_min = rtt
            self.base_rtt_est = rtt
            self._win_start = t
            return
        self._cur_win_min = min(self._cur_win_min, rtt)
        if t - self._win_start >= self.MIN_RTT_WINDOW:
            self.base_rtt_est = self._cur_win_min
            self._win_start = t
            self._cur_win_min = rtt

    def tick(self, obs: dict) -> dict:
        t = int(obs["t"])
        acked = float(obs["acked"])
        lost = float(obs["lost"])
        rtt = float(obs["rtt"])
        timeout = bool(obs["timeout"])

        self._update_base_rtt(t, rtt)
        base = self.base_rtt_est if self.base_rtt_est else rtt

        sent = acked + lost
        loss_frac = (lost / sent) if sent > 0 else 0.0
        self.loss_ewma = (1 - self.LOSS_EWMA_ALPHA) * self.loss_ewma + self.LOSS_EWMA_ALPHA * loss_frac

        diff = self.cwnd * (rtt - base) / rtt if rtt > 0 else 0.0

        if timeout:
            self.cwnd = self.lo
            self.mode = "ss"
            self.recover_until = t + max(1, round(rtt))
        else:
            loss_cut = False
            if self.loss_ewma > self.LOSS_DECREASE_THRESH and t >= self.recover_until:
                factor = max(self.LOSS_DECREASE_FLOOR, 1.0 - 2.0 * self.loss_ewma)
                self.cwnd = max(self.lo, self.cwnd * factor)
                self.recover_until = t + max(1, round(rtt))
                loss_cut = True

            if self.mode == "ss":
                if lost > 0 or diff > self.SS_EXIT_DIFF or loss_cut:
                    self.mode = "ca"
                else:
                    self.cwnd += acked

            if self.mode == "ca" and not loss_cut:
                target = self.cwnd * base / rtt + self.ALPHA_PKTS if rtt > 0 else self.cwnd
                self.cwnd += self.GAIN * (target - self.cwnd) / max(rtt, 1e-6)

        self.cwnd = min(self.hi, max(self.lo, self.cwnd))
        telemetry = {
            "cwnd": self.cwnd,
            "mode": self.mode,
            "diff": diff,
            "loss_ewma": self.loss_ewma,
            "base_rtt_est": base,
        }
        return {"cwnd": self.cwnd, "telemetry": telemetry}
