import math


class Controller:
    """Delay-aware AIMD controller (Vegas-style queue target).

    - Keeps a small standing queue (TARGET packets) when it is alone, so delay stays low.
    - Ignores sparse losses that arrive with little queueing, treating them as
      random loss. This keeps throughput up on lossy paths.
    - Halves the window on congestive loss (loss with a large queue), at most once
      per round trip, as Reno does.
    - Shrinks the window multiplicatively when the loss rate stays high.
    - Drops to the minimum window on a timeout.
    - Drains the queue periodically to re-measure the base RTT, so a change in
      the path's delay is picked up.
    """

    TARGET = 4.0            # packets of our own data allowed to sit in the queue
    HIGH_LOSS = 0.05        # sustained loss rate above which we back off regardless

    def __init__(self, config: dict):
        self.min_cwnd = float(config.get("min_cwnd", 1.0))
        self.max_cwnd = float(config.get("max_cwnd", 2000.0))
        self.cwnd = self._clamp(float(config.get("initial_cwnd", 4.0)))
        self.ss = True
        self.ssthresh = self.max_cwnd
        self.base = None
        self.srtt = None
        self.rate = 0.0
        self.L = 0.0
        self.T = 0.0
        self.rtt_max = None
        self.last_cut = -1e9
        self.last_cong_loss = -1e9
        self.probing = False
        self.probe_end = 0
        self.probe_min = float("inf")
        self.probe_cwnd = self.cwnd
        self.saved_cwnd = self.cwnd
        self.last_probe_end = 0
        self.win_start = 0
        self.cur_min = float("inf")
        self.prev_min = float("inf")
        self.small_ticks = 0

    def _clamp(self, w):
        if w != w:  # NaN guard
            w = self.min_cwnd
        return max(self.min_cwnd, min(self.max_cwnd, w))

    def _out(self, mode, diff=0.0, p=0.0, qd=0.0):
        self.cwnd = self._clamp(self.cwnd)
        return {
            "cwnd": float(self.cwnd),
            "telemetry": {
                "mode": mode,
                "base_rtt": float(self.base) if self.base is not None else 0.0,
                "srtt": float(self.srtt) if self.srtt is not None else 0.0,
                "diff": float(diff),
                "loss_rate": float(p),
                "qdelay": float(qd),
                "ss": int(self.ss),
            },
        }

    def tick(self, obs: dict) -> dict:
        t = int(obs.get("t", 0))
        acked = max(0.0, float(obs.get("acked", 0.0) or 0.0))
        lost = max(0.0, float(obs.get("lost", 0.0) or 0.0))
        rtt = obs.get("rtt", None)
        try:
            rtt = float(rtt)
        except (TypeError, ValueError):
            rtt = None
        if rtt is not None and (rtt != rtt or rtt <= 0 or math.isinf(rtt)):
            rtt = None
        timeout = bool(obs.get("timeout", False))

        # RTT estimators
        if rtt is not None:
            self.srtt = rtt if self.srtt is None else 0.875 * self.srtt + 0.125 * rtt
            self.base = rtt if self.base is None else min(self.base, rtt)
            if self.rtt_max is None:
                self.rtt_max = rtt

        # Timeout: full backoff
        if timeout:
            self.ssthresh = max(self.cwnd / 2.0, 2.0)
            self.cwnd = self.min_cwnd
            self.ss = True
            self.probing = False
            self.last_cut = t
            self.last_cong_loss = t
            self.small_ticks = 0
            return self._out("timeout")

        if self.srtt is None or self.base is None:
            if not self.ss:
                pass
            return self._out("init")

        R = max(self.srtt, 1.0)
        rtt_cur = rtt if rtt is not None else self.srtt

        # rate and loss estimators
        a = min(1.0, 1.0 / R)
        self.rate += (acked - self.rate) * a
        la = min(1.0, 1.0 / (4.0 * R))
        self.L = self.L * (1.0 - la) + lost
        self.T = self.T * (1.0 - la) + acked + lost
        p = self.L / self.T if self.T > 1e-9 else 0.0

        # windowed recent minimum RTT
        if t - self.win_start >= 4.0 * R:
            self.prev_min = self.cur_min
            self.cur_min = float("inf")
            self.win_start = t
        self.cur_min = min(self.cur_min, rtt_cur)
        recent_min = min(self.prev_min, self.cur_min)

        qd = max(0.0, rtt_cur - self.base)
        self.rtt_max = max(rtt_cur, self.rtt_max - (self.rtt_max - self.base) / (16.0 * R))
        diff = self.cwnd * qd / rtt_cur
        send_rate = max(self.cwnd / rtt_cur, 1e-9)
        target_delay = self.TARGET / send_rate

        # ---- RTT probe in progress ----
        if self.probing:
            self.probe_min = min(self.probe_min, rtt_cur)
            if t >= self.probe_end:
                self.probing = False
                old = self.base
                if self.probe_min < float("inf"):
                    self.base = self.probe_min
                self.cwnd = self.saved_cwnd
                if (self.base > old * 1.25
                        and t - self.last_cong_loss > 32.0 * R
                        and p < self.HIGH_LOSS):
                    self.ss = True
                    self.ssthresh = self.max_cwnd
                self.last_probe_end = t
                self.cur_min = rtt_cur
                self.prev_min = rtt_cur
                self.win_start = t
                self.rtt_max = max(rtt_cur, self.base)
                self.small_ticks = 0
                return self._out("probe_end", diff, p, qd)
            self.cwnd = self.probe_cwnd
            return self._out("probe", diff, p, qd)

        # ---- loss response ----
        congestive = (lost > 0
                      and qd > 2.0 * target_delay
                      and qd > 0.5 * (self.rtt_max - self.base))
        cut = False
        if congestive:
            self.last_cong_loss = t
            if t - self.last_cut >= R:
                self.cwnd = self.cwnd * 0.5
                self.ssthresh = max(self.cwnd, 2.0)
                self.ss = False
                self.last_cut = t
                cut = True

        high_loss = p > self.HIGH_LOSS and self.T > 2.0
        if high_loss and not cut:
            factor = 1.0 - min(0.5, 1.5 * p)
            self.cwnd *= factor ** (1.0 / R)
            self.ss = False
            cut = True

        mode = "ca"
        if not cut:
            if self.ss:
                mode = "ss"
                if diff > self.TARGET:
                    self.ss = False
                    est = max(acked, self.rate) * self.base + self.TARGET
                    self.cwnd = min(self.cwnd, max(est, self.min_cwnd))
                elif self.cwnd >= self.ssthresh:
                    self.ss = False
                else:
                    self.cwnd += acked
            else:
                err = self.TARGET - diff
                if err > 0:
                    self.cwnd += min(1.0, 0.5 * err) / R
                else:
                    dec = max(0.5 * err, -0.5 * self.cwnd)
                    self.cwnd += dec / R
                # re-enter slow start if link seems underused for a long time
                if diff < 0.25 * self.TARGET:
                    self.small_ticks += 1
                else:
                    self.small_ticks = 0
                if (self.small_ticks > 8.0 * R
                        and t - self.last_cong_loss > 32.0 * R
                        and p < 0.02):
                    self.ss = True
                    self.ssthresh = self.max_cwnd
                    self.small_ticks = 0

        self.cwnd = self._clamp(self.cwnd)

        # ---- probe triggers ----
        if not self.ss:
            since = t - self.last_probe_end
            periodic = since >= max(30.0 * R, 100.0)
            early = (recent_min < float("inf")
                     and since > 4.0 * R
                     and recent_min - self.base > max(0.25 * self.base, 3.0 * target_delay))
            if periodic or early:
                self.probing = True
                self.saved_cwnd = self.cwnd
                self.probe_cwnd = self._clamp(0.5 * self.cwnd)
                self.probe_min = float("inf")
                self.probe_end = t + max(3, int(1.5 * R) + 2)
                self.cwnd = self.probe_cwnd
                return self._out("probe_start", diff, p, qd)

        return self._out(mode, diff, p, qd)
