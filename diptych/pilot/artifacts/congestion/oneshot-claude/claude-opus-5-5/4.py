"""Delay-targeting congestion controller (FAST/Vegas style) with loss-rate backoff.

It keeps a small fixed backlog (ALPHA packets) at the bottleneck, estimated
from rtt against a base-RTT estimate. Isolated losses are ignored; a high
loss rate or a timeout triggers multiplicative backoff or collapse.
"""

import math


class Controller:
    ALPHA = 2.0          # target backlog of our own packets in the queue
    GAMMA = 0.5          # fraction of the backlog error corrected per RTT
    LOSS_HI = 0.08       # loss rate treated as congestion / high loss
    LOSS_MIN_SAMPLE = 20.0
    PROBE_RTTS = 60.0    # re-measure base RTT if not refreshed for this long

    def __init__(self, config: dict):
        config = config or {}
        self.min_cwnd = float(config.get("min_cwnd", 1.0))
        self.max_cwnd = float(config.get("max_cwnd", 2000.0))
        if self.max_cwnd < self.min_cwnd:
            self.max_cwnd = self.min_cwnd
        self.cwnd = self._clamp(float(config.get("initial_cwnd", 4.0)))

        self.t = -1
        self.ss = True
        self.base = 0.0           # base RTT estimate (0 = unknown)
        self.base_stamp = 0
        self.last_rtt = 0.0
        self.prev_rtt = 0.0       # previous tick's rtt (0 = unknown)

        self.s_lost = 0.0
        self.s_total = 0.0
        self.loss_rate = 0.0
        self.last_cut = -10 ** 9

        # velocity (accelerated increase when the path is clearly underused)
        self.vel = 1.0
        self.low_rounds = 0
        self.round_end = 0.0
        self.round_maxq = 0.0

        # base-RTT step detection
        self.step_t0 = -1
        self.step_r = 0.0
        self.step_min = 0.0
        self.step_rate = 0.0

        # periodic base-RTT probe (drain)
        self.drain_end = -1
        self.drain_min = 0.0
        self.drain_save = 0.0

        self.queued = 0.0
        self.mode = "ss"

    # ------------------------------------------------------------------ utils
    def _clamp(self, w):
        if w != w:
            w = self.min_cwnd
        if w < self.min_cwnd:
            w = self.min_cwnd
        if w > self.max_cwnd:
            w = self.max_cwnd
        return float(w)

    @staticmethod
    def _num(x, default):
        try:
            v = float(x)
        except (TypeError, ValueError):
            return default
        if v != v or v in (float("inf"), float("-inf")):
            return default
        return v

    def _out(self):
        self.cwnd = self._clamp(self.cwnd)
        return {
            "cwnd": self.cwnd,
            "telemetry": {
                "mode": self.mode,
                "base_rtt": float(self.base),
                "queued": float(self.queued),
                "loss_rate": float(self.loss_rate),
                "vel": float(self.vel),
            },
        }

    # ------------------------------------------------------------------- tick
    def tick(self, obs: dict) -> dict:
        t = int(self._num(obs.get("t"), self.t + 1))
        self.t = t
        acked = max(0.0, self._num(obs.get("acked"), 0.0))
        lost = max(0.0, self._num(obs.get("lost"), 0.0))
        rtt_in = self._num(obs.get("rtt"), 0.0)
        timeout = bool(obs.get("timeout", False))

        # ---- timeout: full backoff
        if timeout:
            self.cwnd = self.min_cwnd
            self.ss = True
            self.vel = 1.0
            self.low_rounds = 0
            self.step_t0 = -1
            self.drain_end = -1
            self.s_lost = 0.0
            self.s_total = 0.0
            self.loss_rate = 0.0
            self.prev_rtt = 0.0
            self.queued = 0.0
            self.mode = "timeout"
            return self._out()

        rtt = rtt_in if rtt_in > 0.0 else self.last_rtt
        if rtt <= 0.0:
            self.mode = "wait"
            return self._out()
        self.last_rtt = rtt
        prev_rtt = self.prev_rtt
        self.prev_rtt = rtt
        rtt_t = max(1.0, rtt)

        # ---- loss-rate estimate (about four RTTs of memory)
        d = 1.0 - 1.0 / (4.0 * rtt_t + 1.0)
        self.s_lost = self.s_lost * d + lost
        self.s_total = self.s_total * d + acked + lost
        p = self.s_lost / max(self.s_total, self.LOSS_MIN_SAMPLE)
        self.loss_rate = p

        # ---- base RTT tracking
        if self.base <= 0.0 or rtt < self.base:
            self.base = rtt
            self.base_stamp = t
        elif rtt <= self.base * 1.001:
            self.base_stamp = t

        # ---- high loss rate: multiplicative decrease, at most once per RTT
        if p > self.LOSS_HI and (t - self.last_cut) >= rtt_t:
            f = 1.0 - min(0.5, 1.5 * p)
            self.cwnd = self._clamp(self.cwnd * f)
            if self.drain_end >= 0:
                self.drain_save *= f
            self.last_cut = t
            self.ss = False
            self.vel = 1.0
            self.low_rounds = 0
            self.s_lost = 0.0
            self.s_total = 0.0

        # ---- sudden RTT step: possibly a change in the path's minimum delay
        if (self.step_t0 < 0 and prev_rtt > 0.0
                and rtt - prev_rtt > max(3.0, 0.3 * prev_rtt)):
            if self.drain_end >= 0:
                self.cwnd = self._clamp(self.drain_save)
                self.drain_end = -1
            self.step_t0 = t
            self.step_r = rtt
            self.step_min = rtt
            self.step_rate = self.cwnd / prev_rtt

        if self.step_t0 >= 0:
            if rtt < 0.9 * self.step_r or rtt > 1.03 * self.step_r:
                self.step_t0 = -1      # it was a queue transient, not a path change
            else:
                if rtt < self.step_min:
                    self.step_min = rtt
                if t - self.step_t0 >= self.step_r:
                    if p <= 0.05:
                        # delay stayed flat for a full RTT: re-base, keep the rate
                        self.base = self.step_min
                        self.base_stamp = t
                        self.cwnd = self._clamp(
                            self.step_rate * self.base + self.ALPHA)
                    self.step_t0 = -1
                self.queued = self.cwnd * max(0.0, 1.0 - self.base / rtt)
                self.mode = "step"
                return self._out()

        # ---- base-RTT probe in progress
        if self.drain_end >= 0:
            if rtt < self.drain_min:
                self.drain_min = rtt
            if t >= self.drain_end:
                if self.drain_min < float("inf"):
                    self.base = self.drain_min
                self.base_stamp = t
                self.cwnd = self._clamp(self.drain_save)
                self.drain_end = -1
                self.round_end = t + rtt_t
                self.round_maxq = 0.0
            self.queued = self.cwnd * max(0.0, 1.0 - self.base / rtt)
            self.mode = "probe"
            return self._out()

        queued = self.cwnd * max(0.0, 1.0 - self.base / rtt)
        self.queued = queued

        # ---- start a base-RTT probe when the estimate is stale
        if (not self.ss
                and t - self.base_stamp > max(100.0, self.PROBE_RTTS * self.base)):
            self.drain_save = self.cwnd
            self.drain_min = float("inf")
            self.drain_end = t + int(math.ceil(2.0 * rtt)) + 1
            self.cwnd = self._clamp(0.5 * self.cwnd)
            self.vel = 1.0
            self.low_rounds = 0
            self.mode = "probe"
            return self._out()

        # ---- slow start with delay-based exit
        if self.ss:
            if queued > self.ALPHA:
                self.ss = False
                self.cwnd = self._clamp(
                    self.cwnd * self.base / rtt + self.ALPHA)
                self.round_end = t + rtt_t
                self.round_maxq = 0.0
            else:
                self.cwnd = self._clamp(
                    self.cwnd + min(acked, self.cwnd / rtt_t))
            self.mode = "ss"
            return self._out()

        # ---- steady state: steer own backlog toward ALPHA packets
        delta = self.GAMMA * (self.ALPHA - queued) / rtt_t
        if delta > 0.0:
            if p > self.LOSS_HI:
                delta = 0.0
            elif queued < 0.5 * self.ALPHA:
                delta *= self.vel
        self.cwnd = self._clamp(self.cwnd + delta)

        if queued > self.round_maxq:
            self.round_maxq = queued
        if t >= self.round_end:
            if self.round_maxq < 0.5 * self.ALPHA:
                self.low_rounds += 1
            else:
                self.low_rounds = 0
                self.vel = 1.0
            if self.low_rounds >= 4:
                cap = max(1.0, 0.25 * self.cwnd / (self.GAMMA * self.ALPHA))
                self.vel = min(self.vel * 2.0, cap)
            self.round_maxq = 0.0
            self.round_end = t + rtt_t

        self.mode = "avoid"
        return self._out()
