"""Congestion control controller for a single flow on a shared bottleneck.

Hybrid design: Reno-style exponential/additive growth (so growth rates stay
comparable to the standard algorithm), but with two refinements:

  * A windowed minimum-RTT estimate is used to compute a normalized queueing
    signal (queue_frac = (smoothed_rtt - min_rtt) / min_rtt). Growth is
    tapered and eventually halted as queue_frac rises, so the flow stops
    pushing more packets into the bottleneck well before the buffer is
    full (keeps latency low when alone, and yields early when competing).

  * Loss events are classified as "congestion" (sustained/elevated loss
    rate, or clear queue buildup) versus "incidental" (isolated random
    loss with no queueing signal). Only congestion losses trigger the
    classic multiplicative backoff (at most once per round trip); a
    timeout always collapses the window. Incidental losses are ignored,
    which is what lets the flow keep high throughput on lossy paths where
    the standard Reno behaviour (halve on every loss) would starve itself.
"""

from collections import deque


class Controller:
    def __init__(self, config: dict):
        self.min_cwnd = float(config.get("min_cwnd", 1.0))
        self.max_cwnd = float(config.get("max_cwnd", 2000.0))
        self.cwnd = float(config.get("initial_cwnd", 4.0))
        self.cwnd = max(self.min_cwnd, min(self.max_cwnd, self.cwnd))

        self.slow_start = True
        self.rtt_ewma = None
        self.loss_ewma = 0.0
        self.min_rtt_window = deque()  # (t, rtt)
        self.last_backoff_t = -1e18

        # Tunable constants.
        self.rtt_ewma_alpha = 0.3
        self.loss_ewma_alpha = 0.3
        self.congestion_loss_rate_thresh = 0.08
        self.queue_congestion_thresh = 0.4   # loss classified as congestion if queue_frac exceeds this
        self.ss_exit_queue_frac = 0.3        # exit slow start before overshooting the buffer
        self.growth_taper_start = 0.15       # start tapering CA growth
        self.growth_stop = self.queue_congestion_thresh

    def _update_min_rtt(self, t: int, rtt: float) -> float:
        self.min_rtt_window.append((t, rtt))
        window_ticks = min(2000.0, max(200.0, 20.0 * (self.rtt_ewma or rtt)))
        while self.min_rtt_window and (t - self.min_rtt_window[0][0]) > window_ticks:
            self.min_rtt_window.popleft()
        return min(r for _, r in self.min_rtt_window)

    def tick(self, obs: dict) -> dict:
        t = int(obs["t"])
        acked = float(obs["acked"])
        lost = float(obs["lost"])
        rtt = float(obs["rtt"])
        timeout = bool(obs["timeout"])

        if self.rtt_ewma is None:
            self.rtt_ewma = rtt
        else:
            self.rtt_ewma = self.rtt_ewma_alpha * rtt + (1.0 - self.rtt_ewma_alpha) * self.rtt_ewma

        min_rtt = self._update_min_rtt(t, rtt)
        min_rtt_safe = max(min_rtt, 1e-6)
        queue_frac = max(0.0, self.rtt_ewma - min_rtt) / min_rtt_safe

        total = acked + lost
        loss_rate = (lost / total) if total > 0 else 0.0
        self.loss_ewma = self.loss_ewma_alpha * loss_rate + (1.0 - self.loss_ewma_alpha) * self.loss_ewma

        telemetry = {
            "min_rtt": min_rtt,
            "rtt_ewma": self.rtt_ewma,
            "queue_frac": queue_frac,
            "loss_ewma": self.loss_ewma,
            "slow_start": int(self.slow_start),
        }

        if timeout:
            self.cwnd = self.min_cwnd
            self.slow_start = False
            self.last_backoff_t = t
            telemetry["event"] = "timeout"
            self.cwnd = max(self.min_cwnd, min(self.max_cwnd, self.cwnd))
            telemetry["cwnd"] = self.cwnd
            return {"cwnd": self.cwnd, "telemetry": telemetry}

        can_backoff = (t - self.last_backoff_t) >= max(1.0, self.rtt_ewma)
        congestion_loss = lost > 0 and (
            self.loss_ewma > self.congestion_loss_rate_thresh
            or queue_frac > self.queue_congestion_thresh
        )

        if congestion_loss and can_backoff:
            self.cwnd = max(self.min_cwnd, self.cwnd * 0.5)
            self.last_backoff_t = t
            self.slow_start = False
            telemetry["event"] = "backoff"
        elif self.slow_start:
            if queue_frac > self.ss_exit_queue_frac:
                self.slow_start = False
                telemetry["event"] = "ss_exit"
            else:
                self.cwnd += acked
                telemetry["event"] = "ss_grow"
        else:
            if queue_frac < self.growth_taper_start:
                scale = 1.0
            elif queue_frac < self.growth_stop:
                scale = (self.growth_stop - queue_frac) / (self.growth_stop - self.growth_taper_start)
            else:
                scale = 0.0
            inc = scale * (acked / max(self.cwnd, 1.0))
            self.cwnd += inc
            telemetry["event"] = "ca"

        self.cwnd = max(self.min_cwnd, min(self.max_cwnd, self.cwnd))
        telemetry["cwnd"] = self.cwnd
        return {"cwnd": self.cwnd, "telemetry": telemetry}
