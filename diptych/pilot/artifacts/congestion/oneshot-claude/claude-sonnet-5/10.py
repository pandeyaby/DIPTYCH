from collections import deque


class Controller:
    MIN_RTT_WINDOW_TICKS = 500.0
    BETA_HIGH = 4.0
    BETA_LOW = 1.0
    LOSS_EWMA_ALPHA = 0.15
    LOSS_EWMA_HIGH = 0.08
    GENTLE_FACTOR = 0.8
    STRONG_FACTOR = 0.5
    RTT_EWMA_ALPHA = 0.3
    EPS = 1e-6

    def __init__(self, config: dict):
        self.min_cwnd = float(config.get("min_cwnd", 1.0))
        self.max_cwnd = float(config.get("max_cwnd", 2000.0))
        self.cwnd = float(config.get("initial_cwnd", 4.0))
        self.cwnd = min(max(self.cwnd, self.min_cwnd), self.max_cwnd)

        self.ssthresh = self.max_cwnd
        self.rtt_ewma = None
        # Monotonic deque holding (t, rtt) pairs with strictly increasing rtt,
        # giving an O(1)-amortized sliding-window minimum for the base RTT.
        self._mono = deque()
        self.min_rtt = None
        self.loss_ewma = 0.0
        self.last_decrease_t = -1e18

    def _update_min_rtt(self, t, rtt):
        mono = self._mono
        while mono and mono[-1][1] >= rtt:
            mono.pop()
        mono.append((t, rtt))
        while mono and mono[0][0] <= t - self.MIN_RTT_WINDOW_TICKS:
            mono.popleft()
        self.min_rtt = mono[0][1]

    def tick(self, obs: dict) -> dict:
        t = obs["t"]
        acked = float(obs["acked"])
        lost = float(obs["lost"])
        rtt = float(obs["rtt"])
        timeout = bool(obs["timeout"])

        self.rtt_ewma = rtt if self.rtt_ewma is None else (
            (1 - self.RTT_EWMA_ALPHA) * self.rtt_ewma + self.RTT_EWMA_ALPHA * rtt
        )
        self._update_min_rtt(t, rtt)
        min_rtt = self.min_rtt if self.min_rtt else rtt

        total = acked + lost
        loss_frac = (lost / total) if total > self.EPS else 0.0
        self.loss_ewma = (
            (1 - self.LOSS_EWMA_ALPHA) * self.loss_ewma + self.LOSS_EWMA_ALPHA * loss_frac
        )

        # Vegas-style estimate of how many of our own packets are sitting
        # queued at the bottleneck right now.
        diff = self.cwnd * (1.0 - min_rtt / rtt) if rtt > self.EPS else 0.0
        diff = max(0.0, diff)

        event = "growth"

        if timeout:
            prev_cwnd = self.cwnd
            self.ssthresh = max(self.min_cwnd, prev_cwnd / 2.0)
            self.cwnd = self.min_cwnd
            self.last_decrease_t = t
            event = "timeout"

        elif lost > 0.0:
            can_decrease = (t - self.last_decrease_t) >= max(1.0, self.rtt_ewma)
            if can_decrease:
                # Distinguish congestion-caused loss (queue was visibly
                # building, or loss rate is persistently high) from
                # incidental random loss, which only needs a mild trim.
                congestive = (diff > self.BETA_HIGH) or (self.loss_ewma >= self.LOSS_EWMA_HIGH)
                factor = self.STRONG_FACTOR if congestive else self.GENTLE_FACTOR
                self.cwnd = max(self.min_cwnd, self.cwnd * factor)
                self.ssthresh = self.cwnd
                self.last_decrease_t = t
                event = "loss_congestive" if congestive else "loss_noncongestive"
            else:
                event = "loss_hold"

        else:
            if self.cwnd < self.ssthresh:
                if diff > self.BETA_HIGH:
                    # Queue is building before any loss: leave slow start
                    # proactively instead of overshooting the buffer.
                    self.ssthresh = self.cwnd
                    event = "slow_start_hold"
                else:
                    self.cwnd = self.cwnd + acked
                    event = "slow_start"
            else:
                if diff > self.BETA_HIGH:
                    self.cwnd = max(self.min_cwnd, self.cwnd - 1.0)
                    self.ssthresh = min(self.ssthresh, self.cwnd)
                    event = "delay_backoff"
                elif diff < self.BETA_LOW:
                    self.cwnd = self.cwnd + acked / max(self.cwnd, 1.0)
                    event = "cong_avoid_grow"
                else:
                    self.cwnd = self.cwnd + 0.5 * acked / max(self.cwnd, 1.0)
                    event = "cong_avoid_hold"

        self.cwnd = min(max(self.cwnd, self.min_cwnd), self.max_cwnd)

        telemetry = {
            "t": t,
            "cwnd": self.cwnd,
            "ssthresh": self.ssthresh,
            "min_rtt": min_rtt,
            "rtt_ewma": self.rtt_ewma,
            "diff": diff,
            "loss_ewma": self.loss_ewma,
            "event": event,
        }
        return {"cwnd": self.cwnd, "telemetry": telemetry}
