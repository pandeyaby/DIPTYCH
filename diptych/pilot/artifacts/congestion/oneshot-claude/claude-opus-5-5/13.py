import math


class Controller:
    """Delay-based controller (Vegas-style) with loss classification.

    - Holds a small standing queue: ALPHA to BETA of its own packets.
    - Ignores isolated random losses. A loss counts as congestion when the
      loss rate is high, or when it happens near the largest queue seen
      recently and this flow has a lot of its own packets queued.
    - Congestion loss: halve the window, at most once per RTT.
    - Timeout: drop the window to min_cwnd.
    - Refreshes its minimum RTT with short drain probes. Probes are aligned
      to the global tick index, so flows on the same path probe at the same
      time and all see the true base RTT.
    """

    ALPHA = 2.0
    BETA = 4.0
    TARGET = 3.0

    def __init__(self, config: dict):
        self.min_cwnd = float(config.get("min_cwnd", 1.0))
        self.max_cwnd = float(config.get("max_cwnd", 2000.0))
        if self.max_cwnd < self.min_cwnd:
            self.max_cwnd = self.min_cwnd
        self.cwnd = self._clamp(float(config.get("initial_cwnd", 4.0)))
        self.in_ss = True
        self.ssthresh = self.max_cwnd
        self.min_rtt = None
        self.srtt = None
        self.L = 0.0
        self.A = 0.0
        self.maxq = 0.0
        self.rate = 0.0
        self.last_cut = -1e18
        self.k = 1.0
        self.round_end = 0.0
        self.round_low = True
        self.probing = False
        self.probe_end = 0
        self.saved = self.cwnd
        self.probe_min = float("inf")
        self.last_diff = 0.0
        self.mode = "ss"

    # ------------------------------------------------------------------
    def _clamp(self, w):
        if w != w or w is None:
            w = self.min_cwnd
        if w < self.min_cwnd:
            w = self.min_cwnd
        if w > self.max_cwnd:
            w = self.max_cwnd
        return float(w)

    def _period(self):
        base = self.min_rtt if self.min_rtt is not None else 1.0
        target = max(16.0, 8.0 * base)
        p = 16
        while p < target and p < (1 << 30):
            p *= 2
        return p

    def _out(self, loss_rate=0.0):
        self.cwnd = self._clamp(self.cwnd)
        return {
            "cwnd": float(self.cwnd),
            "telemetry": {
                "mode": self.mode,
                "min_rtt": float(self.min_rtt) if self.min_rtt is not None else -1.0,
                "srtt": float(self.srtt) if self.srtt is not None else -1.0,
                "diff": float(self.last_diff),
                "loss_rate": float(loss_rate),
                "k": float(self.k),
                "maxq": float(self.maxq),
            },
        }

    # ------------------------------------------------------------------
    def tick(self, obs: dict) -> dict:
        try:
            t = int(obs.get("t", 0))
        except Exception:
            t = 0
        acked = obs.get("acked", 0.0)
        lost = obs.get("lost", 0.0)
        rtt = obs.get("rtt", None)
        timeout = bool(obs.get("timeout", False))
        try:
            acked = float(acked)
        except Exception:
            acked = 0.0
        try:
            lost = float(lost)
        except Exception:
            lost = 0.0
        if not (acked == acked) or acked < 0:
            acked = 0.0
        if not (lost == lost) or lost < 0:
            lost = 0.0

        valid_rtt = False
        try:
            rtt = float(rtt)
            if rtt == rtt and rtt > 0 and not math.isinf(rtt):
                valid_rtt = True
        except Exception:
            valid_rtt = False

        if valid_rtt:
            self.srtt = rtt if self.srtt is None else 0.875 * self.srtt + 0.125 * rtt
            if self.min_rtt is None or rtt < self.min_rtt:
                self.min_rtt = rtt

        srtt = self.srtt if self.srtt is not None else 1.0
        srtt = max(1.0, srtt)

        # Loss-rate estimate over roughly the last 4 RTTs.
        decay = 1.0 - 1.0 / (4.0 * srtt + 1.0)
        self.L = self.L * decay + lost
        self.A = self.A * decay + acked + lost
        loss_rate = self.L / self.A if self.A > 0 else 0.0
        high_loss = self.A >= 8.0 and loss_rate > 0.1

        self.rate = 0.9 * self.rate + 0.1 * acked

        # Timeout: full backoff.
        if timeout:
            self.ssthresh = max(2.0, self.cwnd * 0.5) if not self.probing else max(2.0, self.saved * 0.5)
            self.cwnd = self.min_cwnd
            self.in_ss = True
            self.probing = False
            self.k = 1.0
            self.round_low = False
            self.last_cut = t
            self.mode = "timeout"
            return self._out(loss_rate)

        if not valid_rtt or self.min_rtt is None:
            return self._out(loss_rate)

        q = max(0.0, rtt - self.min_rtt)
        diff = self.cwnd * q / rtt
        self.last_diff = diff
        self.maxq = max(q, self.maxq * (1.0 - 1.0 / (64.0 * srtt)))

        # Drain probe to refresh the minimum RTT.
        if self.probing:
            self.mode = "probe"
            if rtt < self.probe_min:
                self.probe_min = rtt
            if t >= self.probe_end:
                if self.probe_min < float("inf"):
                    self.min_rtt = self.probe_min
                self.probing = False
                self.cwnd = self.saved
                self.round_low = False
                self.round_end = t + srtt
            return self._out(loss_rate)

        P = self._period()
        if (not self.in_ss) and t > 0 and t % P == 0:
            self.probing = True
            self.saved = self.cwnd
            self.probe_min = float("inf")
            self.probe_end = t + int(1.5 * self.min_rtt) + 3
            self.cwnd = max(self.min_cwnd, 0.7 * self.cwnd)
            self.mode = "probe"
            return self._out(loss_rate)

        # Loss handling.
        block_increase = False
        if lost > 0:
            congest = high_loss or (
                q >= 0.5 * self.maxq and diff > 2.0 * self.BETA + self.rate
            )
            if congest:
                if t - self.last_cut >= srtt:
                    self.cwnd = self.cwnd * 0.5
                    self.ssthresh = max(2.0, self.cwnd)
                    self.last_cut = t
                    self.in_ss = False
                    self.k = 1.0
                    self.round_low = False
                    self.round_end = t + srtt
                    self.mode = "cut"
                    return self._out(loss_rate)
                block_increase = True

        if high_loss:
            self.k = 1.0

        # Delay-based control.
        if self.in_ss:
            self.mode = "ss"
            if diff > self.BETA or self.cwnd >= self.ssthresh:
                self.in_ss = False
                self.k = 1.0
                self.round_low = False
                self.round_end = t + srtt
            elif not block_increase:
                self.cwnd += min(acked, self.cwnd)

        if not self.in_ss:
            frac = min(1.0, acked / max(self.cwnd, 1e-9))
            if diff < self.ALPHA:
                self.mode = "inc"
                if not block_increase:
                    inc = 1.0 if high_loss else self.k
                    self.cwnd += frac * inc
            elif diff > self.BETA:
                self.mode = "dec"
                dec = min(0.5 * (diff - self.TARGET), 0.25 * self.cwnd)
                self.cwnd -= frac * dec
                self.round_low = False
            else:
                self.mode = "hold"
                self.round_low = False

            if t >= self.round_end:
                if self.round_low and not high_loss and not block_increase:
                    self.k = min(self.k * 2.0, max(1.0, self.cwnd / 4.0))
                else:
                    self.k = 1.0
                self.round_low = True
                self.round_end = t + srtt

        return self._out(loss_rate)
