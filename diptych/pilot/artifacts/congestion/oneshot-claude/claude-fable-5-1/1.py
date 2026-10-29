import collections
import math


class Controller:
    """
    Delay-sensitive AIMD congestion controller.

    Core idea (Vegas-like, but with a drain-to-target backoff):
      * Estimate the path's base RTT as a windowed minimum.
      * Estimate how many of *our* packets sit in the bottleneck queue:
            q = cwnd * (rtt - base) / rtt
      * Grow additively (+1 packet per RTT, doubling in slow start).
      * When q exceeds BETA packets, drop the window so that only ALPHA of
        our packets remain queued (cwnd = cwnd*base/rtt + ALPHA), at most
        once per RTT.  This keeps the standing queue to a handful of
        packets when alone and lets two flows of this kind converge to equal
        queue shares (hence equal rates).
      * Losses are treated as congestion only when the queue is high, when
        the loss rate is high (> 6%), or when we cannot trust our base-RTT
        estimate ("uncertain" mode, entered when a backoff fails to drain the
        queue, i.e. someone else is filling it).  Otherwise an isolated loss
        on an empty queue is ignored as random.
      * Timeouts collapse the window to min_cwnd.
      * A sudden RTT jump that our backoff cannot drain, with no losses and a
        perfectly flat RTT afterwards, is recognised as a change in the base
        RTT; the base is reset and the window rescaled to the new BDP.
    """

    ALPHA = 1.0            # our queued packets left after a backoff
    BETA = 6.0             # back off when our queued packets exceed this
    LOSS_RATE_HIGH = 0.06  # loss rate above which we always halve
    BASE_WINDOW_RTTS = 10.0
    LOSS_WINDOW_RTTS = 4.0
    PROBE_LEN = 2.5        # RTTs after a backoff before judging the drain
    DRAIN_FRAC = 0.6       # queue must fall to <= this fraction to count as drained
    JUMP_RATIO = 1.4       # rtt jump (over ~3 ticks) that flags a base change
    FLAT_TOL = 0.03        # relative rtt flatness required for a base change
    SHIFT_GAP_RTTS = 20.0  # minimum spacing between base-shift resets
    SS_EXIT_Q = 2.0        # queued packets that end slow start

    # ------------------------------------------------------------------ init
    def __init__(self, config):
        cfg = config or {}
        self.min_cwnd = float(cfg.get("min_cwnd", 1.0))
        self.max_cwnd = float(cfg.get("max_cwnd", 2000.0))
        if self.max_cwnd < self.min_cwnd:
            self.max_cwnd = self.min_cwnd
        self.cwnd = self._clamp(float(cfg.get("initial_cwnd", 4.0)))
        self.ssthresh = self.max_cwnd
        self.slow_start = True

        # RTT tracking
        self.srtt = None
        self.last_rtt = None
        self.base = None
        self.base_q = collections.deque()          # monotonic (t, rtt) for window min
        self.recent = collections.deque(maxlen=6)  # recent (t, rtt) for jump detection

        # loss tracking
        self.loss_hist = collections.deque()       # (t, acked, lost)
        self.last_loss_t = -10 ** 9

        # decrease gating
        self.last_dec_t = -10 ** 9
        self.last_dec_rtt = 0.0
        self.next_dec_t = -10 ** 9

        # base reliability / probing
        self.certain = True
        self.probe = None
        self.hold_until = -10 ** 9
        self.jump_t = None
        self.jump_min = None
        self.last_shift_t = -10 ** 9

        # capacity / buffer estimates (only used to shrink BETA on tiny buffers)
        self.c_ewma = 0.0
        self.c_max = 0.0
        self.buf_pkts = None

        # telemetry
        self.event = "init"
        self.n_backoff = 0
        self.n_shift = 0

    # ------------------------------------------------------------------ tick
    def tick(self, obs):
        obs = obs or {}
        t = int(self._num(obs.get("t", 0)))
        acked = max(0.0, self._num(obs.get("acked", 0.0)))
        lost = max(0.0, self._num(obs.get("lost", 0.0)))
        timeout = bool(obs.get("timeout", False))
        rtt_raw = self._num(obs.get("rtt", 0.0))

        if rtt_raw > 0.0:
            self._update_rtt(t, rtt_raw, lost)
        rtt = self.last_rtt if self.last_rtt else 1.0
        srtt = self.srtt if self.srtt else rtt

        # loss / capacity bookkeeping
        self.loss_hist.append((t, acked, lost))
        horizon = t - self.LOSS_WINDOW_RTTS * srtt - 1.0
        while self.loss_hist and self.loss_hist[0][0] < horizon:
            self.loss_hist.popleft()
        self.c_ewma = 0.8 * self.c_ewma + 0.2 * acked
        self.c_max = max(self.c_ewma, self.c_max * 0.995)
        loss_rate = self._loss_rate()
        if lost > 0.0:
            self.last_loss_t = t
            self._update_buffer_estimate(acked, lost, rtt)

        self.event = "steady"
        self._probe_step(t, rtt, acked, lost)

        beta = self._beta()
        alpha = min(self.ALPHA, beta / 3.0)
        q_pkts = self._queued_pkts(rtt)
        reduced = False

        # ---- timeout: full backoff
        if timeout and t >= self.next_dec_t:
            self.ssthresh = max(self.cwnd / 2.0, 2.0 * self.min_cwnd)
            self.cwnd = self.min_cwnd
            self.slow_start = True
            self._mark_decrease(t, srtt)
            self.probe = None
            self.event = "timeout"
            reduced = True

        # ---- loss handling (once per RTT)
        elif lost > 0.0 and t >= self.next_dec_t:
            if loss_rate > self.LOSS_RATE_HIGH:
                self._reduce(t, rtt, self.cwnd * 0.5, "loss_high")
                reduced = True
            elif not self.certain or self.base is None:
                self._reduce(t, rtt, self.cwnd * 0.5, "loss_std")
                reduced = True
            elif q_pkts > beta:
                self._reduce(t, rtt, self.cwnd * self.base / rtt + alpha, "loss_drain")
                reduced = True
            else:
                self.event = "loss_ignored"

        # ---- delay-based backoff / slow-start exit
        if (not reduced and self.base is not None
                and t >= self.next_dec_t and t >= self.hold_until):
            if q_pkts > beta:
                self._reduce(t, rtt, self.cwnd * self.base / rtt + alpha, "delay_drain")
                reduced = True
                if self.jump_t is not None and t - self.jump_t <= 2.0 * rtt:
                    # a suspicious RTT jump: hold further backoffs until judged
                    self.hold_until = t + self.PROBE_LEN * rtt
            elif self.slow_start and q_pkts > self.SS_EXIT_Q:
                self.slow_start = False
                self.ssthresh = self.cwnd
                self.event = "ss_exit"

        # ---- increase
        if not reduced:
            if self.slow_start:
                self.cwnd += acked
                if self.cwnd >= self.ssthresh:
                    self.slow_start = False
            elif self.cwnd > 0.0:
                self.cwnd += acked / self.cwnd

        self.cwnd = self._clamp(self.cwnd)

        telemetry = {
            "cwnd": float(self.cwnd),
            "ssthresh": float(min(self.ssthresh, self.max_cwnd)),
            "rtt": float(rtt),
            "srtt": float(srtt),
            "base_rtt": float(self.base if self.base is not None else 0.0),
            "q_pkts": float(q_pkts),
            "beta": float(beta),
            "loss_rate": float(loss_rate),
            "mode": "certain" if self.certain else "uncertain",
            "slow_start": int(self.slow_start),
            "event": str(self.event),
            "n_backoff": int(self.n_backoff),
            "n_shift": int(self.n_shift),
            "buf_est": float(self.buf_pkts if self.buf_pkts is not None else 0.0),
        }
        return {"cwnd": float(self.cwnd), "telemetry": telemetry}

    # ------------------------------------------------------------ internals
    @staticmethod
    def _num(x):
        try:
            v = float(x)
        except (TypeError, ValueError):
            return 0.0
        if not math.isfinite(v):
            return 0.0
        return v

    def _clamp(self, w):
        if not isinstance(w, float) or not math.isfinite(w):
            return self.min_cwnd
        if w < self.min_cwnd:
            return self.min_cwnd
        if w > self.max_cwnd:
            return self.max_cwnd
        return w

    def _update_rtt(self, t, rtt, lost):
        self.last_rtt = rtt
        if self.srtt is None:
            self.srtt = rtt
        else:
            self.srtt = 0.875 * self.srtt + 0.125 * rtt

        # sudden jump detection (candidate base-RTT change)
        if len(self.recent) >= 3 and not self.slow_start and lost <= 0.0:
            old = self.recent[-3][1]
            if old > 0.0 and rtt > self.JUMP_RATIO * old:
                self.jump_t = t
                self.jump_min = rtt
        if self.jump_t is not None:
            self.jump_min = min(self.jump_min, rtt)
            if t - self.jump_t > 6.0 * self.srtt:
                self.jump_t = None
                self.jump_min = None
        self.recent.append((t, rtt))

        # windowed minimum via monotonic deque
        window = max(self.BASE_WINDOW_RTTS * self.srtt, 20.0)
        q = self.base_q
        while q and q[-1][1] >= rtt:
            q.pop()
        q.append((t, rtt))
        while q and q[0][0] < t - window:
            q.popleft()
        self.base = q[0][1] if q else rtt

    def _queued_pkts(self, rtt):
        if self.base is None or rtt <= 0.0:
            return 0.0
        return max(0.0, self.cwnd * (rtt - self.base) / rtt)

    def _beta(self):
        if self.buf_pkts is None:
            return self.BETA
        return min(self.BETA, max(2.0, 0.3 * self.buf_pkts))

    def _loss_rate(self):
        start = self.last_dec_t + self.last_dec_rtt
        acked = 0.0
        lost = 0.0
        for (ti, a, l) in self.loss_hist:
            if ti >= start:
                acked += a
                lost += l
        tot = acked + lost
        if tot <= 0.0:
            return 0.0
        return lost / tot

    def _update_buffer_estimate(self, acked, lost, rtt):
        tot = acked + lost
        if lost < 3.0 or tot <= 0.0 or lost / tot < 0.1:
            return
        if self.base is None or self.c_max <= 0.0:
            return
        q_total = self.c_max * max(0.0, rtt - self.base)
        if q_total < 1.0:
            return
        if self.buf_pkts is None:
            self.buf_pkts = q_total
        else:
            self.buf_pkts = 0.5 * (self.buf_pkts + q_total)

    def _mark_decrease(self, t, rtt):
        self.last_dec_t = t
        self.last_dec_rtt = max(1.0, rtt)
        self.next_dec_t = t + max(1.0, rtt)

    def _reduce(self, t, rtt, new_cwnd, event):
        before = self.cwnd
        new_cwnd = min(new_cwnd, before)
        new_cwnd = self._clamp(new_cwnd)
        self.cwnd = new_cwnd
        self.ssthresh = max(new_cwnd, 2.0 * self.min_cwnd)
        self.slow_start = False
        self._mark_decrease(t, rtt)
        base = self.base if self.base is not None else rtt
        if self.probe is None:
            self.probe = {
                "t0": t,
                "rtt0": max(1.0, rtt),
                "qd0": max(0.0, rtt - base),
                "base0": base,
                "cwnd_before": before,
                "min": None,
                "max": None,
                "acked": 0.0,
                "lost": 0.0,
            }
        self.event = event
        self.n_backoff += 1

    def _probe_step(self, t, rtt, acked, lost):
        p = self.probe
        if p is None:
            return
        p["acked"] += acked
        p["lost"] += lost
        if t >= p["t0"] + p["rtt0"]:
            p["min"] = rtt if p["min"] is None else min(p["min"], rtt)
            p["max"] = rtt if p["max"] is None else max(p["max"], rtt)
        if t >= p["t0"] + self.PROBE_LEN * p["rtt0"] and p["min"] is not None:
            self.probe = None
            base = self.base if self.base is not None else p["min"]
            tol = max(self.DRAIN_FRAC * p["qd0"], 0.05 * base)
            if p["min"] <= base + tol + 1e-9:
                self.certain = True
                self.event = "drained"
            else:
                self.certain = False
                self.event = "undrained"
                self._maybe_base_shift(t, p)

    def _maybe_base_shift(self, t, p):
        jt = self.jump_t
        if jt is None:
            return
        if jt > p["t0"] or p["t0"] - jt > 2.0 * p["rtt0"]:
            return
        tot = p["acked"] + p["lost"]
        if tot > 0.0 and p["lost"] / tot > 0.03:
            return
        if p["max"] - p["min"] > self.FLAT_TOL * p["min"]:
            return
        srtt = self.srtt if self.srtt else p["rtt0"]
        if t - self.last_shift_t < self.SHIFT_GAP_RTTS * srtt:
            return
        old_base = p["base0"]
        new_base = p["min"]
        if old_base is None or old_base <= 0.0 or new_base <= 1.2 * old_base:
            return
        ratio = min(new_base / old_base, 4.0)

        self.base = new_base
        self.base_q.clear()
        self.base_q.append((t, new_base))
        self.cwnd = self._clamp(p["cwnd_before"] * ratio + self.ALPHA)
        self.ssthresh = max(self.ssthresh, self.cwnd)
        self.slow_start = False
        self.certain = True
        self.last_shift_t = t
        self.jump_t = None
        self.jump_min = None
        self.hold_until = -10 ** 9
        self._mark_decrease(t, new_base)
        self.event = "base_shift"
        self.n_shift += 1
