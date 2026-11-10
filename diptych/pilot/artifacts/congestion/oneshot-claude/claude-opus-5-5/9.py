import math
from collections import deque


class Controller:
    """Delay-based congestion controller with loss safeguards.

    The window follows a FAST-style rule that keeps about ALPHA of the
    flow's own packets in the bottleneck queue. Random losses are ignored.
    The window is halved when a loss comes with a large queue, or when the
    loss rate stays high. Short drain probes re-measure the base RTT so the
    flow can follow changes in the path.
    """

    ALPHA = 2.5            # target own packets queued at the bottleneck
    GAMMA = 0.5            # FAST step size per RTT
    HIGH_LOSS = 0.06       # sustained loss rate treated as congestion
    ACCEL_LOSS = 0.02      # max loss rate allowed for fast (2x/RTT) growth
    PROBE_PERIOD = 25.0    # periodic base-RTT probe interval, in base RTTs
    PROBE_DRAIN = 0.7      # window fraction (of estimated BDP) during probes

    def __init__(self, config: dict):
        self.min_cwnd = float(config.get("min_cwnd", 1.0))
        self.max_cwnd = float(config.get("max_cwnd", 2000.0))
        init = float(config.get("initial_cwnd", 4.0))
        self.cwnd = self._clamp(init)

        self.base = None
        self.srtt = None
        self.hist = deque()  # (t, rtt) samples over the last ~2 RTTs

        self.rate_s = 0.0
        self.max_rate = 0.0

        self.loss_l = 0.0
        self.loss_n = 0.0

        self.last_dec_t = -1e18
        self.last_cong_t = -1e18

        self.in_probe = False
        self.probe_end_t = 0
        self.probe_min = float("inf")
        self.probe_rtt_start = 1.0
        self.saved_w = self.cwnd
        self.last_probe_end = 0
        self.next_periodic = None

        self.mode = "start"

    # ------------------------------------------------------------------
    def _clamp(self, w):
        if not (w == w) or w is None:  # NaN guard
            w = self.min_cwnd
        return max(self.min_cwnd, min(self.max_cwnd, float(w)))

    def _out(self, diff=0.0, lossrate=0.0):
        self.cwnd = self._clamp(self.cwnd)
        tel = {
            "mode": self.mode,
            "base_rtt": float(self.base) if self.base is not None else 0.0,
            "srtt": float(self.srtt) if self.srtt is not None else 0.0,
            "queue_pkts": float(diff),
            "loss_rate": float(lossrate),
            "rate": float(self.rate_s),
            "max_rate": float(self.max_rate),
        }
        return {"cwnd": float(self.cwnd), "telemetry": tel}

    def _start_probe(self, t, rtt, reason):
        self.in_probe = True
        self.mode = "probe_" + reason
        self.saved_w = self.cwnd
        self.probe_rtt_start = max(rtt, self.base)
        self.cwnd = self._clamp(self.PROBE_DRAIN * self.cwnd * self.base / self.probe_rtt_start)
        self.probe_min = float("inf")
        hold = max(3, int(math.ceil(1.5 * max(rtt, 1.0))))
        self.probe_end_t = t + hold

    # ------------------------------------------------------------------
    def tick(self, obs: dict) -> dict:
        t = int(obs.get("t", 0))
        acked = max(0.0, float(obs.get("acked", 0.0) or 0.0))
        lost = max(0.0, float(obs.get("lost", 0.0) or 0.0))
        rtt_raw = obs.get("rtt", 0.0)
        try:
            rtt_raw = float(rtt_raw)
        except (TypeError, ValueError):
            rtt_raw = 0.0
        timeout = bool(obs.get("timeout", False))

        valid_rtt = (rtt_raw > 0.0) and math.isfinite(rtt_raw) and acked > 0.0

        # --- RTT estimators -------------------------------------------
        if valid_rtt:
            if self.srtt is None:
                self.srtt = rtt_raw
            else:
                self.srtt += 0.125 * (rtt_raw - self.srtt)
            if self.base is None or rtt_raw < self.base:
                self.base = rtt_raw
            if self.next_periodic is None:
                self.next_periodic = t + self.PROBE_PERIOD * max(self.base, 1.0)
            self.hist.append((t, rtt_raw))
        srtt = self.srtt if self.srtt is not None else 1.0
        srtt = max(srtt, 1.0)
        horizon = max(2.0, 2.0 * srtt)
        while self.hist and self.hist[0][0] < t - horizon:
            self.hist.popleft()

        # --- loss-rate estimate (~4 RTT memory) ------------------------
        f = min(0.5, 1.0 / (4.0 * srtt))
        self.loss_l = (1.0 - f) * self.loss_l + lost
        self.loss_n = (1.0 - f) * self.loss_n + acked + lost
        lossrate = self.loss_l / self.loss_n if self.loss_n > 1e-9 else 0.0

        # --- delivery rate --------------------------------------------
        g = min(1.0, 1.0 / srtt)
        self.rate_s += g * (acked - self.rate_s)
        self.max_rate = max(self.rate_s, self.max_rate * (1.0 - 1.0 / (10.0 * srtt)))

        # --- timeout: full backoff ------------------------------------
        if timeout:
            self.cwnd = self.min_cwnd
            self.last_dec_t = t
            self.last_cong_t = t
            if self.in_probe:
                self.in_probe = False
                self.last_probe_end = t
            self.mode = "timeout"
            return self._out(0.0, lossrate)

        if self.base is None:
            # No RTT information yet; hold the window.
            self.mode = "start"
            return self._out(0.0, lossrate)

        rtt = rtt_raw if valid_rtt else max(srtt, self.base)
        rtt = max(rtt, self.base)

        # --- base-RTT probe in progress --------------------------------
        if self.in_probe:
            if valid_rtt:
                self.probe_min = min(self.probe_min, rtt_raw)
            if t >= self.probe_end_t:
                if math.isfinite(self.probe_min):
                    self.base = self.probe_min
                restore = self.saved_w * self.base / max(self.probe_rtt_start, self.base) + self.ALPHA
                self.cwnd = self._clamp(min(self.saved_w, restore))
                self.in_probe = False
                self.last_probe_end = t
                self.next_periodic = t + self.PROBE_PERIOD * max(self.base, 1.0)
                self.hist.clear()
                self.mode = "fast"
            return self._out(0.0, lossrate)

        # --- normal operation -----------------------------------------
        diff = self.cwnd * (rtt - self.base) / rtt  # own packets queued
        can_dec = (t - self.last_dec_t) >= srtt

        if lost > 0.0 and diff > 3.0 * self.ALPHA and can_dec:
            # Loss with a large standing queue: congestion.
            self.cwnd *= 0.5
            self.last_dec_t = t
            self.last_cong_t = t
            self.mode = "cong_loss"
        elif lossrate > self.HIGH_LOSS and self.loss_l >= 2.0 and can_dec:
            # Persistently high loss: back off regardless of delay.
            self.cwnd *= 0.5
            self.last_dec_t = t
            self.last_cong_t = t
            self.mode = "high_loss"
        elif acked <= 0.0 and lost > 0.0 and can_dec:
            self.cwnd *= 0.5
            self.last_dec_t = t
            self.last_cong_t = t
            self.mode = "all_lost"
        elif acked > 0.0:
            frac = min(1.0, acked / max(self.cwnd, 1e-9))
            accel_ok = (diff < 0.5 * self.ALPHA
                        and lossrate < self.ACCEL_LOSS
                        and (t - self.last_cong_t) > 2.0 * srtt)
            if accel_ok:
                self.cwnd *= 2.0 ** frac
                self.mode = "accel"
            else:
                target = self.cwnd * self.base / rtt + self.ALPHA
                self.cwnd += self.GAMMA * (target - self.cwnd) * frac
                self.mode = "fast"

        self.cwnd = self._clamp(self.cwnd)

        # --- base-RTT probe triggers ----------------------------------
        if self.hist:
            min_recent = min(r for _, r in self.hist)
        else:
            min_recent = self.base
        since_probe = t - self.last_probe_end
        collapsed = (self.max_rate > 0.0
                     and self.rate_s < 0.6 * self.max_rate
                     and min_recent > 1.3 * self.base
                     and lossrate < self.HIGH_LOSS
                     and since_probe >= 3.0 * srtt
                     and len(self.hist) >= 2)
        if collapsed:
            self._start_probe(t, rtt, "collapse")
        elif self.next_periodic is not None and t >= self.next_periodic and since_probe >= 3.0 * srtt:
            self._start_probe(t, rtt, "periodic")

        return self._out(diff, lossrate)
