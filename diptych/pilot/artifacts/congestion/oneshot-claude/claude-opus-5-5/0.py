import math


class Controller:
    """Delay-led AIMD congestion controller.

    * Grows like Reno (slow start, then one packet per round trip).
    * Backs off multiplicatively when queueing delay (rtt above the estimated
      base rtt) exceeds a small fraction of the base rtt, draining the queue
      it built.  This keeps the standing queue short, yields to buffer-filling
      flows, and gives synchronised AIMD (fair) between flows of this kind.
    * Isolated losses are ignored when the path has a background loss rate
      (random loss); they are treated as congestion on otherwise clean paths
      or when the short-term loss rate is high.
    * Timeout collapses the window to the minimum.
    * A step increase of the path's base rtt is recognised by the rtt staying
      flat while elevated, and the window is restored from the remembered
      delivery rate.
    """

    THR = 0.06          # queueing-delay threshold, fraction of base rtt
    SS_THR = 0.25       # slow-start exit threshold, fraction of base rtt
    DRAIN = 0.94        # post-backoff target, fraction of estimated BDP
    MIN_FACTOR = 0.5    # largest single delay backoff
    HEAVY_RATE = 0.05   # short-term loss rate treated as congestion
    HEAVY_COUNT = 3.0
    SEVERE_RATE = 0.12
    HEAVY_BETA = 0.7
    CLEAN_P0 = 0.001    # long-run loss rate below which a path is "clean"
    CLEAN_BETA = 0.6
    LONG_PKTS = 4000.0  # memory (packets) of the long-run loss estimate
    FLAT_EPS = 3e-4     # relative rtt tolerance for "flat"
    BASE_WINDOW_RTTS = 50

    def __init__(self, config: dict):
        cfg = config or {}
        self.min_cwnd = float(cfg.get("min_cwnd", 1.0))
        self.max_cwnd = float(cfg.get("max_cwnd", 2000.0))
        if self.max_cwnd < self.min_cwnd:
            self.max_cwnd = self.min_cwnd
        self.cwnd = self._clamp(float(cfg.get("initial_cwnd", 4.0)))

        self.n = 0
        self.slow_start = True
        self.next_dec = 0

        self.base = None
        self.last_rtt = None
        self.bmin_cur = float("inf")
        self.bmin_prev = float("inf")
        self.bucket_end = None

        self.rate = None
        self.bw_cur = 0.0
        self.bw_prev = 0.0
        self.bw_end = None

        self.la = 0.0
        self.aa = 0.0
        self.s_lost = 0.0
        self.s_sent = 0.0

        self.flat_ref = None
        self.flat_len = 0
        self.last_flat_reset = -10 ** 9
        self.event = "init"

    # ------------------------------------------------------------------ utils
    def _clamp(self, w):
        if w != w or w in (float("inf"), float("-inf")):
            return self.min_cwnd
        if w < self.min_cwnd:
            return self.min_cwnd
        if w > self.max_cwnd:
            return self.max_cwnd
        return w

    @staticmethod
    def _num(x):
        try:
            v = float(x)
        except (TypeError, ValueError):
            return 0.0
        if v != v or v in (float("inf"), float("-inf")):
            return 0.0
        return v

    def _out(self, qdelay):
        self.cwnd = self._clamp(self.cwnd)
        tot = self.la + self.aa
        return {
            "cwnd": float(self.cwnd),
            "telemetry": {
                "cwnd": float(self.cwnd),
                "mode": "slow_start" if self.slow_start else "avoid",
                "event": self.event,
                "base_rtt": float(self.base) if self.base else 0.0,
                "qdelay": float(qdelay),
                "rate": float(self.rate) if self.rate else 0.0,
                "loss_short": float(self.la / tot) if tot > 0 else 0.0,
                "loss_long": float(self.s_lost / self.s_sent)
                if self.s_sent > 0 else 0.0,
            },
        }

    # ------------------------------------------------------------------- tick
    def tick(self, obs: dict) -> dict:
        t = self.n
        self.n += 1
        acked = max(0.0, self._num(obs.get("acked", 0.0)))
        lost = max(0.0, self._num(obs.get("lost", 0.0)))
        timeout = bool(obs.get("timeout", False))
        raw_rtt = self._num(obs.get("rtt", 0.0))
        self.event = "none"

        # ---- timeout: full backoff ----------------------------------------
        if timeout:
            self.cwnd = self.min_cwnd
            self.slow_start = True
            self.la = 0.0
            self.aa = 0.0
            self.flat_ref = None
            self.flat_len = 0
            self.bw_cur = 0.0
            self.bw_prev = 0.0
            self.next_dec = t
            self.event = "timeout"
            return self._out(0.0)

        if raw_rtt > 0.0:
            rtt = raw_rtt
        elif self.last_rtt is not None:
            rtt = self.last_rtt
        else:
            rtt = 1.0
        self.last_rtt = rtt

        # ---- base rtt (windowed minimum) ----------------------------------
        if self.base is None:
            self.base = rtt
            self.bmin_cur = rtt
            self.bucket_end = t + max(20, int(self.BASE_WINDOW_RTTS * rtt))
        if rtt < self.bmin_cur:
            self.bmin_cur = rtt
        if t >= self.bucket_end:
            self.bmin_prev = self.bmin_cur
            self.bmin_cur = rtt
            self.base = min(self.bmin_prev, rtt)
            self.bucket_end = t + max(
                20, int(self.BASE_WINDOW_RTTS * self.base))
        if rtt < self.base:
            self.base = rtt
        base = self.base

        # ---- delivery rate ------------------------------------------------
        if self.rate is None:
            self.rate = acked
        else:
            a = 1.0 / max(2.0, rtt / 2.0)
            self.rate += a * (acked - self.rate)
        if self.bw_end is None:
            self.bw_end = t + max(8, int(8 * rtt))
        if t >= self.bw_end:
            self.bw_prev = self.bw_cur
            self.bw_cur = 0.0
            self.bw_end = t + max(8, int(8 * rtt))
        if self.rate > self.bw_cur:
            self.bw_cur = self.rate
        bw_long = max(self.bw_cur, self.bw_prev)

        # ---- loss accounting ----------------------------------------------
        d = 1.0 - 1.0 / max(4.0, rtt)
        self.la = self.la * d + lost
        self.aa = self.aa * d + acked
        sent = acked + lost
        if sent > 0.0:
            dl = math.exp(-sent / self.LONG_PKTS)
            self.s_lost = self.s_lost * dl + lost
            self.s_sent = self.s_sent * dl + sent
        tot = self.la + self.aa
        short_rate = self.la / tot if tot > 0.0 else 0.0
        long_rate = self.s_lost / self.s_sent if self.s_sent > 0.0 else 0.0

        # ---- flat-rtt tracking (detects a step in the base rtt) -----------
        if acked > 0.0:
            if (self.flat_ref is None
                    or abs(rtt - self.flat_ref) > self.FLAT_EPS * self.flat_ref):
                self.flat_ref = rtt
                self.flat_len = 0
            else:
                self.flat_len += 1
        flat_elevated = (self.flat_ref is not None
                         and self.flat_ref > base * (1.0 + 0.5 * self.THR))

        if (flat_elevated
                and self.flat_len >= max(3.0, 1.25 * rtt)
                and short_rate < self.HEAVY_RATE
                and t - self.last_flat_reset >= 10.0 * rtt):
            # rtt has not moved for more than a round trip although it sits
            # above the base estimate: the path's minimum delay changed.
            self.base = self.flat_ref
            base = self.base
            self.bmin_cur = base
            self.bmin_prev = base
            self.bucket_end = t + max(20, int(self.BASE_WINDOW_RTTS * base))
            self.last_flat_reset = t
            self.flat_len = 0
            restore = 0.98 * bw_long * base
            if restore > self.cwnd:
                self.cwnd = self._clamp(restore)
            self.next_dec = t + max(1, int(math.ceil(rtt)))
            self.event = "base_reset"
            return self._out(0.0)

        qdelay = max(0.0, rtt - base)
        thr = self.THR * base
        can_dec = t >= self.next_dec
        cooldown = max(1, int(math.ceil(min(rtt, 3.0 * base))))

        warmed = tot >= max(2.0, 0.5 * self.cwnd)
        heavy = warmed and (
            (short_rate > self.HEAVY_RATE and self.la >= self.HEAVY_COUNT)
            or short_rate > self.SEVERE_RATE)
        light = (lost > 0.0 and qdelay > 0.25 * thr
                 and self.s_sent > 0.0 and long_rate < self.CLEAN_P0)

        reduced = False
        if heavy and can_dec:
            self.cwnd *= self.HEAVY_BETA
            self.s_lost = max(0.0, self.s_lost - self.la)
            self.la = 0.0
            self.aa = 0.0
            self.slow_start = False
            self.next_dec = t + cooldown
            reduced = True
            self.event = "loss_heavy"
        elif light and can_dec:
            self.cwnd *= self.CLEAN_BETA
            self.slow_start = False
            self.next_dec = t + cooldown
            reduced = True
            self.event = "loss_clean"
        elif self.slow_start:
            if qdelay > self.SS_THR * base:
                self._delay_cut(rtt, base)
                self.slow_start = False
                self.next_dec = t + cooldown
                reduced = True
                self.event = "ss_exit"
        elif qdelay > thr and can_dec:
            holding = flat_elevated and self.flat_len >= 0.6 * rtt
            if not holding:
                self._delay_cut(rtt, base)
                self.next_dec = t + cooldown
                reduced = True
                self.event = "delay"

        # ---- growth -------------------------------------------------------
        if not reduced and acked > 0.0:
            if self.slow_start:
                self.cwnd += 0.7 * acked
            elif qdelay <= thr:
                self.cwnd += min(1.0, acked / max(self.cwnd, 1e-9))

        return self._out(qdelay)

    def _delay_cut(self, rtt, base):
        w = self.cwnd
        by_rate = self.DRAIN * (self.rate or 0.0) * base
        by_rtt = w * self.DRAIN * base / max(rtt, 1e-9)
        target = max(by_rate, by_rtt, self.MIN_FACTOR * w)
        if target < w:
            self.cwnd = target
