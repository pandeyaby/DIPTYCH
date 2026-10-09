import math
from collections import deque


class Controller:
    """
    Delay-based congestion controller with loss discrimination.

    Core ideas
    ----------
    * Track the base RTT with a windowed minimum filter (plus a detector for
      abrupt, persistent RTT floor shifts so a change in the path's minimum
      delay is re-learned quickly).
    * Estimate our own share of the bottleneck queue
          q = cwnd * (rtt - base_rtt) / rtt   (packets)
      and keep it inside a small fixed band [ALPHA, BETA].  This keeps the
      queue (and latency) low when alone and makes us yield to loss-based
      flows that fill the buffer.
    * Losses are treated as congestion only when there is evidence for it
      (queue is high, loss rate is high, or the RTT sits at the top of its
      recent range).  Isolated random losses on an otherwise idle queue are
      ignored, so lossy paths are used efficiently.  Sustained high loss
      still halves the window every round trip.
    * Timeouts collapse the window to the minimum, like the standard algorithm.
    * When the link looks underused for consecutive round trips the additive
      increase ramps up (1, 2, 4, ... packets per RTT, capped at ~10% of cwnd)
      so large pipes and path changes are tracked reasonably fast without
      being more aggressive than slow start.
    """

    ALPHA = 3.0               # own queued packets: below -> link underused
    BETA = 6.0                # own queued packets: above -> back off
    LOSS_RATE_THRESH = 0.05   # loss rate above which losses always mean congestion
    PROBE_CAP = 0.10          # max fraction of cwnd added per RTT when probing
    WINDOW_ROUNDS = 30.0      # base-RTT min filter window (in base RTTs)
    WINDOW_MIN_TICKS = 150.0  # ... but at least this many ticks

    # ------------------------------------------------------------------ #
    def __init__(self, config):
        config = config or {}
        self.min_cwnd = self._f(config.get("min_cwnd", 1.0), 1.0)
        self.max_cwnd = self._f(config.get("max_cwnd", 2000.0), 2000.0)
        if self.min_cwnd <= 0:
            self.min_cwnd = 1.0
        if self.max_cwnd < self.min_cwnd:
            self.max_cwnd = self.min_cwnd
        self.cwnd = self._clamp(self._f(config.get("initial_cwnd", 4.0), 4.0))

        self.ssthresh = float("inf")
        self.slow_start = True

        self.t = -1
        self.last_rtt = 0.0
        self.base_rtt = 0.0
        self.prev_samples = []        # last two RTT samples
        self.jump = None              # [level, delta, t0, confirmations]

        self.hist = deque()           # (t, acked, lost) per tick, ~3 RTTs
        self.round_hist = []          # (end_t, rtt_min, rtt_max) per round
        self.round_start = None
        self.round_len = 1.0
        self.cur_rmin = float("inf")
        self.cur_rmax = 0.0
        self.cur_samples = 0

        self.last_loss_react_t = -1e18
        self.last_loss_t = -1e18
        self.last_ack_t = 0.0
        self.underutil_rounds = 0
        self.last_gain = 0.0

    # ------------------------------------------------------------------ #
    @staticmethod
    def _f(v, default):
        try:
            x = float(v)
        except (TypeError, ValueError):
            return default
        if math.isnan(x) or math.isinf(x):
            return default
        return x

    def _nonneg(self, v):
        x = self._f(v, 0.0)
        return x if x > 0 else 0.0

    def _clamp(self, x):
        if x != x or math.isinf(x):   # NaN / inf guard
            x = self.min_cwnd
        return min(self.max_cwnd, max(self.min_cwnd, x))

    def _window(self):
        ref = self.base_rtt if self.base_rtt > 0 else self.last_rtt
        return max(self.WINDOW_ROUNDS * ref, self.WINDOW_MIN_TICKS)

    def _base(self, t):
        w = self._window()
        m = self.cur_rmin if self.cur_samples > 0 else float("inf")
        for (end_t, rmin, _rmax) in self.round_hist:
            if end_t >= t - w and rmin < m:
                m = rmin
        self.base_rtt = m if not math.isinf(m) else 0.0
        return self.base_rtt

    def _rtt_max(self, t):
        w = self._window()
        m = self.cur_rmax if self.cur_samples > 0 else 0.0
        for (end_t, _rmin, rmax) in self.round_hist:
            if end_t >= t - w and rmax > m:
                m = rmax
        return m

    def _rtt_sample(self, t, rtt):
        self.last_rtt = rtt
        if rtt < self.cur_rmin:
            self.cur_rmin = rtt
        if rtt > self.cur_rmax:
            self.cur_rmax = rtt
        self.cur_samples += 1

        base = self._base(t)

        # Detector for an abrupt, persistent rise of the RTT floor (base RTT
        # change).  A queue build-up rises gradually and keeps rising; a
        # base-RTT change jumps once and then stays flat.  Loss around the
        # jump means buffer overflow, not a path change.
        if self.jump is not None:
            level, delta, t0, n = self.jump
            if abs(rtt - level) <= 0.25 * delta and self.last_loss_t < t0 - 1:
                n += 1
                if n >= 2:
                    self.round_hist = []
                    self.cur_rmin = min(level, rtt)
                    self.cur_rmax = max(level, rtt)
                    self.cur_samples = 1
                    self.underutil_rounds = 0
                    self.jump = None
                    self._base(t)
                else:
                    self.jump[3] = n
            else:
                self.jump = None
        elif self.prev_samples and base > 0:
            ref = min(self.prev_samples)
            delta = rtt - ref
            if delta >= max(0.5 * base, 2.0) and self.last_loss_t < t - 1:
                self.jump = [rtt, delta, t, 0]

        self.prev_samples.append(rtt)
        if len(self.prev_samples) > 2:
            self.prev_samples.pop(0)

    def _stats(self, t, rtt_now):
        tot_a = 0.0
        tot_l = 0.0
        rec_a = 0.0
        rec_n = 0
        for (ti, a, l) in self.hist:
            tot_a += a
            tot_l += l
            if ti > t - rtt_now:
                rec_a += a
                rec_n += 1
        denom = tot_a + tot_l
        loss_rate = tot_l / denom if denom > 0 else 0.0
        rate = rec_a / rec_n if rec_n > 0 else 0.0
        return rate, loss_rate

    # ------------------------------------------------------------------ #
    def tick(self, obs):
        obs = obs or {}
        try:
            t = int(obs.get("t", self.t + 1))
        except (TypeError, ValueError):
            t = self.t + 1
        if t <= self.t:
            t = self.t + 1
        self.t = t

        acked = self._nonneg(obs.get("acked"))
        lost = self._nonneg(obs.get("lost"))
        rtt = self._nonneg(obs.get("rtt"))
        timeout = bool(obs.get("timeout", False))
        event = "none"

        if acked > 0:
            self.last_ack_t = t
        if lost > 0:
            self.last_loss_t = t

        have_sample = rtt > 0 and acked > 0
        if have_sample:
            self._rtt_sample(t, rtt)

        rtt_now = self.last_rtt if self.last_rtt > 0 else max(rtt, 1.0)
        base = self._base(t)
        if base <= 0:
            base = rtt_now

        # Per-tick history for rate / loss-rate estimation.
        self.hist.append((t, acked, lost))
        keep = max(3.0 * rtt_now, 3.0)
        while len(self.hist) > 1 and self.hist[0][0] < t - keep:
            self.hist.popleft()
        rate_est, loss_rate = self._stats(t, rtt_now)

        q_est = self.cwnd * max(0.0, rtt_now - base) / rtt_now
        bdp_est = rate_est * base
        target = 0.5 * (self.ALPHA + self.BETA)

        if self.round_start is None:
            self.round_start = t
            self.round_len = max(1.0, rtt_now)
        round_done = (t - self.round_start) >= self.round_len

        # ---------------- timeout: full backoff ----------------------- #
        if timeout and acked <= 0 and (t - self.last_ack_t) >= rtt_now:
            self.ssthresh = max(0.5 * self.cwnd, 2.0)
            self.cwnd = self.min_cwnd
            self.slow_start = True
            self.underutil_rounds = 0
            self.last_loss_react_t = t
            self.jump = None
            event = "timeout"

        # ---------------- loss handling ------------------------------- #
        elif lost > 0 and (t - self.last_loss_react_t) >= rtt_now:
            rng = max(0.0, self._rtt_max(t) - base)
            top_of_range = (rng * rate_est > 3.0 * self.BETA
                            and rtt_now >= base + 0.8 * rng)
            congestion = (loss_rate > self.LOSS_RATE_THRESH
                          or q_est > 2.0 * self.BETA
                          or top_of_range)
            if congestion:
                new = max(0.5 * self.cwnd, self.min_cwnd)
                self.cwnd = new
                self.ssthresh = new
                self.slow_start = False
                self.underutil_rounds = 0
                self.last_loss_react_t = t
                event = "loss_cut"
            else:
                event = "loss_ignored"

        # ---------------- window growth ------------------------------- #
        gain = 0.0
        if event not in ("timeout", "loss_cut"):
            if self.slow_start:
                if have_sample and q_est > self.BETA:
                    new = bdp_est + target if bdp_est > 0 else 0.5 * self.cwnd
                    new = min(new, self.cwnd)
                    new = max(new, 0.25 * self.cwnd, self.min_cwnd)
                    self.cwnd = new
                    self.ssthresh = new
                    self.slow_start = False
                    self.underutil_rounds = 0
                    event = "ss_exit"
                else:
                    self.cwnd += acked
                    if self.cwnd >= self.ssthresh:
                        self.slow_start = False
            else:
                if acked > 0 and q_est < self.BETA:
                    if q_est < self.ALPHA:
                        n = max(0, self.underutil_rounds - 1)
                        gain = min(2.0 ** min(n, 20),
                                   max(4.0, self.PROBE_CAP * self.cwnd))
                    else:
                        gain = 1.0
                    self.cwnd += gain * acked / max(self.cwnd, 1.0)
        self.last_gain = gain

        # ---------------- end of round -------------------------------- #
        if round_done:
            if (self.cur_samples > 0 and not self.slow_start
                    and event in ("none", "loss_ignored")
                    and (t - self.last_loss_react_t) >= rtt_now):
                if q_est > self.BETA:
                    cand = self.cwnd - 0.5 * (q_est - target)
                    if bdp_est > 0:
                        cand = min(cand, bdp_est + target)
                    new = max(cand, 0.5 * self.cwnd, self.min_cwnd)
                    new = min(new, self.cwnd)
                    self.cwnd = new
                    self.underutil_rounds = 0
                    event = "delay_cut"
                elif q_est < self.ALPHA:
                    self.underutil_rounds += 1
                else:
                    self.underutil_rounds = 0

            if self.cur_samples > 0:
                self.round_hist.append((t, self.cur_rmin, self.cur_rmax))
            w = self._window()
            self.round_hist = [r for r in self.round_hist if r[0] >= t - w]
            if len(self.round_hist) > 600:
                self.round_hist = self.round_hist[-600:]
            self.round_start = t
            self.round_len = max(1.0, rtt_now)
            self.cur_rmin = float("inf")
            self.cur_rmax = 0.0
            self.cur_samples = 0

        self.cwnd = self._clamp(self.cwnd)

        ssth = self.ssthresh if not math.isinf(self.ssthresh) else self.max_cwnd
        telemetry = {
            "cwnd": float(self.cwnd),
            "base_rtt": float(base),
            "rtt": float(rtt_now),
            "q_est": float(q_est),
            "rate_est": float(rate_est),
            "bdp_est": float(bdp_est),
            "loss_rate": float(loss_rate),
            "slow_start": int(self.slow_start),
            "ssthresh": float(ssth),
            "gain": float(gain),
            "underutil_rounds": int(self.underutil_rounds),
            "event": str(event),
        }
        return {"cwnd": float(self.cwnd), "telemetry": telemetry}
