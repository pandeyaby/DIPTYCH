"""Congestion control: delay-aware AIMD with loss-rate classification.

Core idea (Reno-style AIMD) is kept so the algorithm is TCP-friendly and
fair against copies of itself, but two extra signals are layered on top:

  * A relative-delay signal (current RTT vs. a rolling estimate of the
    minimum/base RTT) is used to back off *before* the bottleneck queue is
    full, and to throttle the increase rate while the queue is building.
    This keeps self-induced queueing low and reduces the damage done to
    any standard flow sharing the link.

  * A slowly-updated loss-rate estimate is used to tell isolated/random
    loss (ignored, so the flow keeps most of its throughput on a lossy
    path) apart from a high, sustained loss rate (which forces a strong
    cut of the window, satisfying the "rate reduction under high loss"
    requirement) from ordinary single losses caused by a full queue
    (handled by the delay signal, which usually fires first).

A multiplicative decrease is applied at most once per round trip, and a
reported timeout collapses the window to the minimum, mirroring standard
behaviour for full backoff.
"""

from collections import deque


class Controller:
    # Delay thresholds, expressed as (rtt - min_rtt) / min_rtt.
    _DELAY_MILD = 0.25
    _DELAY_HIGH = 0.75

    # Loss-rate (EWMA) thresholds.
    _LOSS_IGNORE = 0.03
    _LOSS_HIGH = 0.12

    _LOSS_EWMA_ALPHA = 0.1
    _RTT_WINDOW_HORIZON = 300  # ticks over which we re-probe the min RTT
    _SEVERE_MULT = 0.4
    _NORMAL_MULT = 0.5
    _MILD_INCREASE_SCALE = 0.25

    def __init__(self, config: dict):
        self.min_cwnd = float(config.get("min_cwnd", 1.0))
        self.max_cwnd = float(config.get("max_cwnd", 2000.0))
        self.cwnd = float(config.get("initial_cwnd", 4.0))
        self.cwnd = max(self.min_cwnd, min(self.max_cwnd, self.cwnd))

        self.slow_start = True
        self.loss_ewma = 0.0
        self.last_decrease_tick = -1_000_000_000
        self.rtt_window = deque()  # (tick, rtt) samples

    def _min_rtt(self, t: float, rtt: float) -> float:
        self.rtt_window.append((t, rtt))
        while self.rtt_window and (t - self.rtt_window[0][0]) > self._RTT_WINDOW_HORIZON:
            self.rtt_window.popleft()
        return min(r for _, r in self.rtt_window)

    def tick(self, obs: dict) -> dict:
        t = obs["t"]
        acked = float(obs["acked"])
        lost = float(obs["lost"])
        rtt = max(float(obs["rtt"]), 1e-6)
        timeout = bool(obs["timeout"])

        if timeout:
            self.cwnd = self.min_cwnd
            self.slow_start = True
            self.loss_ewma = 0.0
            self.last_decrease_tick = t
            self.cwnd = max(self.min_cwnd, min(self.max_cwnd, self.cwnd))
            return {
                "cwnd": self.cwnd,
                "telemetry": {
                    "phase": "timeout",
                    "action": "full_backoff",
                    "cwnd": self.cwnd,
                    "rtt": rtt,
                    "loss_ewma": self.loss_ewma,
                },
            }

        total = acked + lost
        cur_loss_frac = (lost / total) if total > 0 else 0.0
        if total > 0:
            self.loss_ewma = (
                self._LOSS_EWMA_ALPHA * cur_loss_frac
                + (1.0 - self._LOSS_EWMA_ALPHA) * self.loss_ewma
            )

        min_rtt = self._min_rtt(t, rtt)
        queue_ratio = max(0.0, (rtt - min_rtt) / min_rtt) if min_rtt > 0 else 0.0

        severe = self.loss_ewma >= self._LOSS_HIGH
        moderate_loss = lost > 0 and self.loss_ewma >= self._LOSS_IGNORE and not severe
        high_delay = queue_ratio >= self._DELAY_HIGH
        mild_delay = queue_ratio >= self._DELAY_MILD

        can_decrease = (t - self.last_decrease_tick) >= max(1.0, round(rtt))

        action = "increase"
        if severe and can_decrease:
            self.cwnd *= self._SEVERE_MULT
            self.last_decrease_tick = t
            self.slow_start = False
            action = "severe_decrease"
        elif (moderate_loss or high_delay) and can_decrease:
            self.cwnd *= self._NORMAL_MULT
            self.last_decrease_tick = t
            self.slow_start = False
            action = "decrease"
        else:
            if self.slow_start and not mild_delay:
                self.cwnd += acked
                action = "slow_start"
            else:
                self.slow_start = False
                if mild_delay:
                    increase = (acked / self.cwnd) * self._MILD_INCREASE_SCALE
                    action = "slow_increase"
                else:
                    increase = acked / self.cwnd
                    action = "increase"
                self.cwnd += increase

        self.cwnd = max(self.min_cwnd, min(self.max_cwnd, self.cwnd))

        return {
            "cwnd": self.cwnd,
            "telemetry": {
                "phase": "slow_start" if self.slow_start else "congestion_avoidance",
                "action": action,
                "cwnd": self.cwnd,
                "rtt": rtt,
                "min_rtt": min_rtt,
                "queue_ratio": queue_ratio,
                "loss_ewma": self.loss_ewma,
            },
        }
