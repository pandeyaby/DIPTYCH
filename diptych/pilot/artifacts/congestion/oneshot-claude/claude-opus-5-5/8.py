import math


class Controller:
    """Delay-gated AIMD with random-loss tolerance.

    - Normal operation: aim for a small standing queue measured as RTT above
      the minimum RTT. When the queueing delay goes over the target, cut the
      window to about 0.9x the estimated share of the bandwidth-delay
      product. This empties the queue on each cycle, so every flow
      re-measures the base RTT.
    - Growth: additive. When the queue has stayed empty for several round
      trips, the increments get larger, but every flow adds the same amount,
      so fairness is kept.
    - A loss counts as congestion only when the queue is near the largest
      seen or the smoothed loss rate is high. Otherwise it is treated as
      random and ignored.
    - A timeout collapses the window to min_cwnd.
    - If the RTT stays high and steady after two cuts in a row, the base RTT
      has changed. The new value is adopted and the window regrows quickly.
    """

    def __init__(self, config):
        self.min_cwnd = float(config.get("min_cwnd", 1.0))
        self.max_cwnd = float(config.get("max_cwnd", 2000.0))
        if self.max_cwnd < self.min_cwnd:
            self.max_cwnd = self.min_cwnd
        self.cwnd = self._clamp(float(config.get("initial_cwnd", 4.0)))
        self.ssthresh = math.inf
        self.in_ss = True

        self.minrtt = None
        self.maxrtt = None
        self.srtt = None
        self.frtt = None
        self.dr = 0.0

        self.loss_l = 0.0
        self.loss_a = 0.0

        self.last_dec = -1e18
        self.k = 0
        self.epoch_end = -1.0
        self.epoch_maxqd = 0.0

        self.obs_start = None
        self.obs_end = None
        self.obs_min = math.inf
        self.prev_elev = None

        self.event = "init"

    # ------------------------------------------------------------------ utils
    def _clamp(self, w):
        if w != w:  # NaN
            w = self.min_cwnd
        return max(self.min_cwnd, min(self.max_cwnd, w))

    @staticmethod
    def _num(x, default=0.0):
        try:
            v = float(x)
        except (TypeError, ValueError):
            return default
        if v != v or v in (math.inf, -math.inf):
            return default
        return v

    # ------------------------------------------------------------------- tick
    def tick(self, obs):
        t = self._num(obs.get("t", 0), 0.0)
        acked = max(0.0, self._num(obs.get("acked", 0.0)))
        lost = max(0.0, self._num(obs.get("lost", 0.0)))
        rtt = self._num(obs.get("rtt", 0.0))
        timeout = bool(obs.get("timeout", False))
        self.event = "none"

        # ---- RTT bookkeeping
        if rtt > 0.0:
            if self.minrtt is None:
                self.minrtt = rtt
                self.maxrtt = rtt
                self.srtt = rtt
                self.frtt = rtt
            else:
                if rtt < self.minrtt:
                    self.minrtt = rtt
                if rtt > self.maxrtt:
                    self.maxrtt = rtt
                self.srtt += (rtt - self.srtt) / 8.0
                self.frtt += 0.5 * (rtt - self.frtt)
            if self.obs_end is not None and t >= self.obs_start:
                if rtt < self.obs_min:
                    self.obs_min = rtt

        srtt_v = max(1.0, self.srtt if self.srtt is not None else 1.0)

        # ---- loss-rate EWMA (~8 RTT memory)
        d = 1.0 - 1.0 / (8.0 * srtt_v + 1.0)
        self.loss_l = self.loss_l * d + lost
        self.loss_a = self.loss_a * d + acked
        tot = self.loss_l + self.loss_a
        loss_rate = self.loss_l / tot if tot > 1e-9 else 0.0

        # ---- delivery-rate estimate (packets per tick)
        g = min(1.0, 2.0 / (srtt_v + 1.0))
        self.dr += g * (acked - self.dr)

        # ---- queueing-delay estimate and target
        if self.minrtt is not None:
            minrtt = self.minrtt
            span = max(0.0, self.maxrtt - minrtt)
            qd = max(0.0, self.frtt - minrtt)
            base_t = 0.05 * minrtt
            if self.dr > 1e-6:
                base_t = max(base_t, 2.0 / self.dr)
            base_t = min(base_t, 0.25 * minrtt)
            target = base_t
            if span > 0.0:
                target = min(target, 0.3 * span)
            target = max(target, 0.02 * minrtt, 1e-6)
            # let a stale large max RTT decay slowly
            if span > 4.0 * base_t:
                self.maxrtt -= (span - 4.0 * base_t) * 2e-4
                span = max(0.0, self.maxrtt - minrtt)
        else:
            minrtt = None
            span = 0.0
            qd = 0.0
            target = 1.0

        # ---- per-RTT epoch: count round trips with an empty queue
        if t >= self.epoch_end:
            if minrtt is not None and self.epoch_maxqd < 0.5 * target:
                self.k += 1
            else:
                self.k = 0
            self.epoch_maxqd = 0.0
            self.epoch_end = t + srtt_v
        if qd > self.epoch_maxqd:
            self.epoch_maxqd = qd

        # ---- base-RTT change detection (end of the post-cut observation window)
        if self.obs_end is not None and t >= self.obs_end and minrtt is not None:
            m = self.obs_min
            if m < math.inf and m > minrtt + max(0.5 * target, 0.05 * minrtt):
                if (self.prev_elev is not None and
                        abs(m - self.prev_elev) <= max(0.5 * target, 0.03 * m)):
                    old_span = span
                    self.minrtt = m
                    self.maxrtt = max(self.maxrtt, m + old_span)
                    self.prev_elev = None
                    self.k = 4
                    self.event = "base_change"
                    minrtt = m
                    qd = max(0.0, self.frtt - minrtt)
                else:
                    self.prev_elev = m
            else:
                self.prev_elev = None
            self.obs_end = None
            self.obs_start = None
            self.obs_min = math.inf

        # ---- control
        if timeout or (acked <= 0.0 and lost > 0.0):
            self.ssthresh = max(2.0, self.cwnd / 2.0)
            self.cwnd = self.min_cwnd
            self.in_ss = True
            self.last_dec = t
            self.k = 0
            self.obs_end = None
            self.obs_start = None
            self.obs_min = math.inf
            self.event = "timeout"
        else:
            congestive = False
            if lost > 0.0:
                if loss_rate > 0.05:
                    congestive = True
                elif minrtt is not None and span > 0.0 and \
                        qd > max(2.0 * target, 0.7 * span):
                    congestive = True

            if congestive and t - self.last_dec >= srtt_v:
                self.cwnd = self.cwnd * 0.5
                self.ssthresh = max(self.min_cwnd, self.cwnd)
                self.in_ss = False
                self.last_dec = t
                self.k = 0
                self.obs_end = None
                self.obs_start = None
                self.obs_min = math.inf
                self.event = "loss_halve"
            elif (minrtt is not None and qd > target and
                  t - self.last_dec >= (srtt_v if self.in_ss else 2.0 * srtt_v)):
                est = self.cwnd * minrtt / max(self.frtt, minrtt)
                if self.dr > 1e-6:
                    est = min(est, self.dr * minrtt)
                new = 0.9 * est
                if new < self.cwnd:
                    self.cwnd = new
                self.in_ss = False
                self.ssthresh = max(self.min_cwnd, self.cwnd)
                self.last_dec = t
                self.k = 0
                self.obs_start = t + srtt_v
                self.obs_end = t + 2.0 * srtt_v
                self.obs_min = math.inf
                self.event = "delay_drain"
            elif acked > 0.0:
                if self.in_ss and self.cwnd < self.ssthresh:
                    self.cwnd += acked
                    self.event = "slow_start"
                else:
                    self.in_ss = False
                    if self.k < 3:
                        inc = 1.0
                    else:
                        inc = min(2.0 ** min(self.k - 2, 8), 256.0)
                        inc = min(inc, max(self.cwnd, 8.0))
                    self.cwnd += inc * acked / max(self.cwnd, 1.0)
                    self.event = "grow"

        self.cwnd = self._clamp(self.cwnd)

        telemetry = {
            "cwnd": float(self.cwnd),
            "minrtt": float(self.minrtt) if self.minrtt is not None else -1.0,
            "srtt": float(srtt_v),
            "qdelay": float(qd),
            "target": float(target),
            "loss_rate": float(loss_rate),
            "rate": float(self.dr),
            "k": int(self.k),
            "slow_start": int(1 if self.in_ss else 0),
            "event": str(self.event),
        }
        return {"cwnd": float(self.cwnd), "telemetry": telemetry}
