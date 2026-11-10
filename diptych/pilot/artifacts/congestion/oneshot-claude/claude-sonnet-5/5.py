"""Congestion control: delay-aware AIMD hybrid.

Design summary
--------------
- A windowed minimum-RTT filter estimates the path's base (queue-free) RTT,
  adapting to legitimate path changes (RTT or capacity shifts) without being
  fooled by transient congestion.
- A Vegas-style "diff" (estimated packets this flow has queued) drives
  growth: grow fast when there is no measured queueing, grow gently when a
  little queueing is building, and hold once queueing crosses a packet
  threshold. This caps self-induced queueing delay before the buffer fills.
- An exponentially-weighted loss rate, sample-size weighted to resist
  quantization noise from small per-tick packet counts, drives multiplicative
  backoff only once it is persistently high -- so isolated random loss is
  mostly ignored while sustained high loss still forces the window down hard.
- A timeout triggers an immediate full backoff to the minimum window.
"""

from collections import deque


class Controller:
    def __init__(self, config: dict):
        self.min_cwnd = float(config.get("min_cwnd", 1.0))
        self.max_cwnd = float(config.get("max_cwnd", 2000.0))
        self.cwnd = self._clamp(float(config.get("initial_cwnd", 4.0)))

        self.slow_start = True

        self._rtt_samples = deque()  # (tick, rtt) windowed min-rtt filter
        self._rtt_window_ticks = 1000.0
        self.base_rtt = None

        self.loss_ewma = 0.0
        self._loss_ewma_alpha = 0.25
        self._loss_thresh = 0.05
        self._loss_gain = 3.0
        self._decrease_floor = 0.3

        self._alpha_pkts = 2.0  # below this: grow gently
        self._gamma_pkts = 4.0  # above this in slow start: exit slow start

        self.last_decrease_tick = -1e18

    def _clamp(self, cwnd: float) -> float:
        return min(max(cwnd, self.min_cwnd), self.max_cwnd)

    def _update_base_rtt(self, t: int, rtt: float) -> float:
        self._rtt_samples.append((t, rtt))
        cutoff = t - self._rtt_window_ticks
        while self._rtt_samples and self._rtt_samples[0][0] < cutoff:
            self._rtt_samples.popleft()
        self.base_rtt = min(r for _, r in self._rtt_samples)
        return self.base_rtt

    def tick(self, obs: dict) -> dict:
        t = obs["t"]
        acked = float(obs["acked"])
        lost = float(obs["lost"])
        rtt = float(obs["rtt"])
        timeout = bool(obs["timeout"])

        base_rtt = self._update_base_rtt(t, rtt)
        if base_rtt <= 0:
            base_rtt = max(rtt, 1e-6)

        total = acked + lost
        loss_rate = (lost / total) if total > 0 else 0.0
        if total > 0:
            w = self._loss_ewma_alpha * min(1.0, total / 4.0)
            self.loss_ewma = (1.0 - w) * self.loss_ewma + w * loss_rate

        decreased = False

        if timeout:
            self.cwnd = self.min_cwnd
            self.slow_start = True
            self.loss_ewma = 0.0
            self.last_decrease_tick = t
            decreased = True
        else:
            can_decrease = (t - self.last_decrease_tick) >= max(1.0, base_rtt)
            if can_decrease and self.loss_ewma > self._loss_thresh:
                factor = max(
                    self._decrease_floor, 1.0 - self._loss_gain * self.loss_ewma
                )
                self.cwnd *= factor
                self.last_decrease_tick = t
                self.slow_start = False
                decreased = True

        diff = 0.0
        if not timeout:
            safe_rtt = max(rtt, 1e-6)
            diff = self.cwnd * (1.0 - base_rtt / safe_rtt)

            if not decreased:
                if self.slow_start:
                    if diff > self._gamma_pkts:
                        self.slow_start = False
                    else:
                        self.cwnd += acked
                if not self.slow_start and not (diff > self._gamma_pkts and False):
                    # congestion-avoidance growth (also applies the same
                    # tick slow start was just exited)
                    if not self.slow_start:
                        if diff <= 0.0:
                            self.cwnd += acked
                        elif diff < self._alpha_pkts:
                            self.cwnd += acked / max(self.cwnd, 1.0)
                        # else: hold, queueing is in the tolerated band or above

        self.cwnd = self._clamp(self.cwnd)

        telemetry = {
            "cwnd": self.cwnd,
            "mode": "slow_start" if self.slow_start else "congestion_avoidance",
            "base_rtt": base_rtt,
            "diff": diff,
            "loss_ewma": self.loss_ewma,
            "decreased": 1 if decreased else 0,
            "timeout": 1 if timeout else 0,
        }
        return {"cwnd": self.cwnd, "telemetry": telemetry}
