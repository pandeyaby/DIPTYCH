"""Congestion control: delay-aware, loss-rate-smoothed AIMD.

Reno-style slow start / congestion avoidance, but:
  * exits slow start early on a delay signal (keeps queues short when alone),
  * reacts to a *smoothed* loss rate rather than any single loss (so a small
    background random-loss rate is mostly ignored, while a sustained high
    loss rate still forces a strong, fast backoff),
  * tracks a windowed minimum RTT so it re-learns the path's base delay
    after the base RTT or the bottleneck capacity changes,
  * collapses to a minimal window on a timeout,
  * limits any one backoff to at most once per round trip.
"""

from collections import deque


class Controller:
    MIN_RTT_WINDOW = 50

    LOSS_EWMA_ALPHA = 0.1
    LOSS_THRESH = 0.05

    SS_DELAY_EXIT_RATIO = 1.5
    SS_LOSS_EXIT_FACTOR = 0.5
    SS_DELAY_EXIT_FACTOR = 0.8

    CA_DELAY_PREVENT_RATIO = 2.0
    CA_DELAY_BACKOFF_FACTOR = 0.85
    CA_LOSS_BACKOFF_FACTOR = 0.5

    def __init__(self, config: dict):
        self.min_cwnd = float(config.get("min_cwnd", 1.0))
        self.max_cwnd = float(config.get("max_cwnd", 2000.0))
        self.initial_cwnd = float(config.get("initial_cwnd", 4.0))

        self.cwnd = self._clamp(self.initial_cwnd)
        self.ssthresh = self.max_cwnd
        self.in_slow_start = True

        self.rtt_window = deque(maxlen=self.MIN_RTT_WINDOW)
        self.loss_ewma = 0.0
        self.ticks_since_backoff = 1e9

    def _clamp(self, cwnd: float) -> float:
        return max(self.min_cwnd, min(self.max_cwnd, cwnd))

    def tick(self, obs: dict) -> dict:
        acked = float(obs.get("acked", 0.0))
        lost = float(obs.get("lost", 0.0))
        rtt = float(obs.get("rtt", 1.0))
        timeout = bool(obs.get("timeout", False))

        self.ticks_since_backoff += 1.0

        total = acked + lost
        if total > 0.0:
            sample_loss = lost / total
            self.loss_ewma = (
                (1.0 - self.LOSS_EWMA_ALPHA) * self.loss_ewma
                + self.LOSS_EWMA_ALPHA * sample_loss
            )

        self.rtt_window.append(rtt)
        recent_min_rtt = min(self.rtt_window)
        delay_ratio = rtt / recent_min_rtt if recent_min_rtt > 0 else 1.0

        event = "growth"

        if timeout:
            self.cwnd = self._clamp(min(2.0, self.max_cwnd))
            self.ssthresh = self.cwnd
            self.in_slow_start = True
            self.ticks_since_backoff = 0.0
            event = "timeout"
        elif self.in_slow_start:
            self.cwnd += acked
            high_loss = self.loss_ewma > self.LOSS_THRESH
            delay_exit = delay_ratio > self.SS_DELAY_EXIT_RATIO
            if high_loss:
                self.ssthresh = self._clamp(self.cwnd * self.SS_LOSS_EXIT_FACTOR)
                self.cwnd = self.ssthresh
                self.in_slow_start = False
                self.ticks_since_backoff = 0.0
                event = "ss_loss_exit"
            elif delay_exit:
                self.ssthresh = self._clamp(self.cwnd * self.SS_DELAY_EXIT_FACTOR)
                self.cwnd = self.ssthresh
                self.in_slow_start = False
                self.ticks_since_backoff = 0.0
                event = "ss_delay_exit"
            else:
                self.cwnd = self._clamp(self.cwnd)
        else:
            rtt_est = max(1.0, rtt)
            can_backoff = self.ticks_since_backoff >= rtt_est

            high_loss = self.loss_ewma > self.LOSS_THRESH
            queue_building = delay_ratio > self.CA_DELAY_PREVENT_RATIO

            if can_backoff and high_loss:
                self.cwnd = self._clamp(self.cwnd * self.CA_LOSS_BACKOFF_FACTOR)
                self.ssthresh = self.cwnd
                self.ticks_since_backoff = 0.0
                event = "loss_backoff"
            elif can_backoff and queue_building:
                self.cwnd = self._clamp(self.cwnd * self.CA_DELAY_BACKOFF_FACTOR)
                self.ssthresh = self.cwnd
                self.ticks_since_backoff = 0.0
                event = "delay_backoff"
            else:
                if self.cwnd > 0:
                    self.cwnd += acked / self.cwnd
                self.cwnd = self._clamp(self.cwnd)

        self.cwnd = self._clamp(self.cwnd)

        telemetry = {
            "event": event,
            "slow_start": 1 if self.in_slow_start else 0,
            "loss_ewma": self.loss_ewma,
            "recent_min_rtt": recent_min_rtt,
            "delay_ratio": delay_ratio,
            "ssthresh": self.ssthresh,
            "cwnd": self.cwnd,
        }

        return {"cwnd": self.cwnd, "telemetry": telemetry}
