import math
from collections import deque


class Controller:
    """Delay-based congestion controller with FAST-style window updates and loss-tolerant logic.

    - Keeps about ALPHA packets of its own in the bottleneck queue, so the queue stays low.
    - Ignores sparse random loss. Backs off when the loss rate is high or when a loss
      comes with a large queue.
    - Drops to min_cwnd on a timeout.
    - Probes the minimum RTT from time to time so it can follow changes in the path.
    """

    ALPHA = 3.0            # target packets this flow keeps queued
    GAMMA = 0.5            # FAST gain per round trip
    GROWTH = 0.15          # extra multiplicative growth per RTT when the queue is short
    HIGH_LOSS = 0.05       # loss-rate threshold that counts as congestion
    PROBE_INTERVAL = 10.0  # srtts between periodic min-RTT probes

    def __init__(self, config: dict):
        self.min_cwnd = float(config.get("min_cwnd", 1.0))
        self.max_cwnd = float(config.get("max_cwnd", 2000.0))
        self.w = self._clamp(float(config.get("initial_cwnd", 4.0)))
        self.base = math.inf
        self.base_stamp = 0
        self.srtt = None
        self.in_ss = True
        self.ssthresh = math.inf
        self.last_reduce = -10 ** 9
        self.loss_l = 0.0
        self.loss_a = 0.0
        self.probing = False
        self.probe_start = 0
        self.probe_len = 0.0
        self.saved_w = self.w
        self.drain_min = math.inf
        self.last_probe_end = 0
        self.recent = deque()  # (t, rtt, w)
        self.q = 0.0
        self.event = "init"

    def _clamp(self, w):
        if not (w == w):  # NaN guard
            w = self.min_cwnd
        return max(self.min_cwnd, min(self.max_cwnd, w))

    def _out(self):
        self.w = self._clamp(self.w)
        lr = self.loss_l / self.loss_a if self.loss_a > 0 else 0.0
        return {
            "cwnd": float(self.w),
            "telemetry": {
                "base_rtt": float(self.base) if self.base != math.inf else -1.0,
                "srtt": float(self.srtt) if self.srtt is not None else -1.0,
                "queue_est": float(self.q),
                "loss_rate": float(lr),
                "slow_start": int(self.in_ss),
                "probing": int(self.probing),
                "event": self.event,
            },
        }

    def tick(self, obs: dict) -> dict:
        t = int(obs.get("t", 0))
        acked = max(0.0, float(obs.get("acked", 0.0) or 0.0))
        lost = max(0.0, float(obs.get("lost", 0.0) or 0.0))
        rtt = float(obs.get("rtt", 0.0) or 0.0)
        timeout = bool(obs.get("timeout", False))
        self.event = "none"

        valid = (rtt > 0.0) and (acked > 0.0) and not timeout and rtt == rtt
        if valid:
            if self.srtt is None:
                self.srtt = rtt
            else:
                self.srtt = 0.75 * self.srtt + 0.25 * rtt
            if rtt < self.base:
                self.base = rtt
                self.base_stamp = t
            elif rtt <= self.base * 1.01:
                self.base_stamp = t
            self.recent.append((t, rtt, self.w))
        srtt = self.srtt if self.srtt is not None else max(rtt, 1.0)
        srtt = max(srtt, 1e-6)
        window = 2.0 * srtt
        while self.recent and t - self.recent[0][0] > window:
            self.recent.popleft()

        # Loss-rate estimate: packet-weighted EWMA over about 8 RTTs.
        decay = 1.0 - min(0.5, 1.0 / (8.0 * srtt))
        self.loss_l = self.loss_l * decay + lost
        self.loss_a = self.loss_a * decay + acked + lost
        loss_rate = self.loss_l / self.loss_a if self.loss_a > 0 else 0.0

        # Timeout: full backoff.
        if timeout:
            if self.w > self.min_cwnd:
                self.ssthresh = max(2.0, self.w / 2.0)
            if self.probing:
                self.probing = False
                self.last_probe_end = t
            self.w = self.min_cwnd
            self.in_ss = True
            self.last_reduce = t
            self.event = "timeout"
            return self._out()

        base = self.base if self.base != math.inf else (rtt if rtt > 0 else 1.0)

        # Min-RTT probe (drain) in progress.
        if self.probing:
            if valid:
                self.drain_min = min(self.drain_min, rtt)
            if loss_rate > self.HIGH_LOSS and lost > 0 and t - self.last_reduce >= srtt:
                self.saved_w *= 0.5
                self.last_reduce = t
            elapsed = t - self.probe_start
            done = elapsed >= self.probe_len
            if valid and elapsed >= 1 and rtt <= base * 1.02:
                done = True
            if done:
                if self.drain_min != math.inf and self.drain_min > self.base:
                    self.base = self.drain_min
                self.base_stamp = t
                self.w = self.saved_w
                self.probing = False
                self.last_probe_end = t
                self.recent.clear()
                self.event = "probe_end"
            return self._out()

        rtt_s = srtt
        q = self.w * max(0.0, 1.0 - base / rtt_s) if rtt_s > 0 else 0.0
        self.q = q
        alpha = self.ALPHA

        # Decide whether to probe the minimum RTT.
        if not self.in_ss and self.base != math.inf:
            start_probe = False
            if t - self.base_stamp > self.PROBE_INTERVAL * srtt:
                start_probe = True
            elif self.recent and t - self.last_probe_end > 4.0 * srtt:
                span = self.recent[-1][0] - self.recent[0][0]
                if span >= 1.5 * srtt:
                    rmin = min(r for _, r, _ in self.recent)
                    if rmin > 1.3 * base and self.w * (1.0 - base / rmin) > 3.0 * alpha:
                        start_probe = True
            if start_probe:
                wmax = max([self.w] + [x for _, _, x in self.recent])
                self.saved_w = wmax
                self.w = max(self.min_cwnd, 0.5 * self.w)
                self.probing = True
                self.probe_start = t
                self.probe_len = max(2.0, srtt)
                self.drain_min = math.inf
                self.event = "probe_start"
                return self._out()

        # React to loss, at most once per RTT.
        if lost > 0 and t - self.last_reduce >= srtt:
            if loss_rate > self.HIGH_LOSS:
                self.w *= 0.5
                self.last_reduce = t
                self.in_ss = False
                self.event = "loss_high_rate"
            elif q > 3.0 * alpha:
                self.w *= 0.7
                self.last_reduce = t
                self.in_ss = False
                self.event = "loss_congestion"
            else:
                self.event = "loss_ignored"
            if self.event != "loss_ignored":
                return self._out()

        if not valid:
            return self._out()

        # Update the window.
        if self.in_ss:
            if q < alpha and self.w < self.ssthresh:
                self.w += min(acked, self.w / max(1.0, rtt_s) * 2.0 + 1.0)
            else:
                self.in_ss = False
        else:
            inc = self.GAMMA * (alpha - q)
            if q < alpha:
                inc += self.GROWTH * self.w * (1.0 - q / alpha)
            inc = min(inc, self.w)  # at most doubles per RTT
            self.w += inc / max(1.0, rtt_s)

        return self._out()
