"""Hybrid delay/loss congestion controller for a single flow.

Design summary
--------------
* Primary congestion signal is queueing delay: dq = rtt - min_rtt, compared
  with a target theta = 10% of the minimum RTT.  All flows sharing a
  bottleneck observe the same dq, so additive increase below the target and a
  synchronised multiplicative decrease above it give AIMD fairness while the
  synchronised backoff drains the queue, which keeps every flow's min-RTT
  estimate honest.
* When the queue is (nearly) empty the window also grows multiplicatively so
  bandwidth freed by a path change is re-filled quickly.
* Loss is treated as congestion only when the loss rate is high (sustained
  > 4%) or when losses coincide with the largest recently seen queueing delay
  (i.e. a full buffer).  Low-rate random loss at an empty queue is ignored.
* A sudden step in RTT that cannot be explained by queue growth is treated as
  a change of the path's base delay and the min-RTT estimate is reset.
* An RTT and a half without any acknowledgement collapses the window to the
  minimum (one packet) and restarts slow start.
"""

import math
from collections import deque


class Controller:
    # ------------------------------------------------------------------ init
    def __init__(self, config):
        cfg = config or {}
        self.min_cwnd = self._as_float(cfg.get("min_cwnd"), 1.0)
        self.max_cwnd = self._as_float(cfg.get("max_cwnd"), 2000.0)
        if self.max_cwnd < self.min_cwnd:
            self.max_cwnd = self.min_cwnd
        self.cwnd = self._clamp(self._as_float(cfg.get("initial_cwnd"), 4.0))

        # ---- tunables ----
        self.target_frac = 0.10       # target queueing delay, fraction of min RTT
        self.beta_delay = 0.75        # multiplicative decrease on delay signal
        self.beta_loss = 0.50         # multiplicative decrease on heavy loss
        self.mi_gain = 0.05           # multiplicative increase per RTT when queue empty
        self.ai_base = 1.0            # additive increase (packets per RTT) near target
        self.ai_boost = 3.0           # extra additive increase when far below target
        self.loss_rate_thresh = 0.04  # sustained loss rate treated as congestion
        self.q_floor = 2.0            # don't back off further once we hold <= this in queue

        # ---- state ----
        self._t = -1
        self.established = False
        self.slow_start = True
        self.min_rtt = None
        self.last_rtt = None
        self.prev_rtt = None
        self.rtt_window = deque()     # monotonic min of (t, rtt)
        self.rate_window = deque()    # monotonic max of (t, delivery rate)
        self.dq_window = deque()      # monotonic max of (t, dq)
        self.hist = deque()           # (t, acked, lost, dq or -1)
        self.last_md_t = -1e9
        self.md_rtt = 1.0
        self.loss_ignore_until = -1.0
        self.timeout_run = 0
        self.last_event = "init"
        self.n_md = 0

    # --------------------------------------------------------------- helpers
    @staticmethod
    def _as_float(v, default):
        try:
            f = float(v)
        except (TypeError, ValueError):
            return default
        if not math.isfinite(f):
            return default
        return f

    @staticmethod
    def _as_int(v, default):
        try:
            return int(v)
        except (TypeError, ValueError):
            return default

    def _clamp(self, w):
        if not math.isfinite(w):
            w = self.min_cwnd
        return max(self.min_cwnd, min(self.max_cwnd, w))

    @staticmethod
    def _push_min(dq, t, v):
        while dq and dq[-1][1] >= v:
            dq.pop()
        dq.append((t, v))

    @staticmethod
    def _push_max(dq, t, v):
        while dq and dq[-1][1] <= v:
            dq.pop()
        dq.append((t, v))

    @staticmethod
    def _expire(dq, t, window):
        while dq and dq[0][0] < t - window:
            dq.popleft()

    def _btlbw(self):
        return self.rate_window[0][1] if self.rate_window else 0.0

    def _rate_sample(self, t, rtt):
        n = max(1, int(round(rtt)))
        total = 0.0
        count = 0
        for tt, a, _l, _d in reversed(self.hist):
            if t - tt >= n:
                break
            total += a
            count += 1
        if count <= 0:
            return 0.0
        return total / float(n)

    def _dq_slope(self, t, dq, rtt):
        k = max(2, int(round(rtt / 2.0)))
        for tt, _a, _l, d in reversed(self.hist):
            if d < 0:
                continue
            if t - tt >= k:
                return (dq - d) / float(t - tt)
        return 0.0

    def _loss_rate(self, t, rtt):
        window = max(20.0, 4.0 * rtt)
        start = max(t - window, self.loss_ignore_until)
        la = 0.0
        ll = 0.0
        for tt, a, l, _d in reversed(self.hist):
            if tt <= start:
                break
            la += a
            ll += l
        tot = la + ll
        return (ll / tot if tot > 0 else 0.0), ll

    def _md(self, t, rtt, factor, event):
        self.cwnd = self._clamp(self.cwnd * factor)
        self.last_md_t = t
        self.md_rtt = max(1.0, rtt)
        self.n_md += 1
        self.last_event = event

    def _reset_estimates(self):
        self.rate_window.clear()
        self.dq_window.clear()
        self.last_rtt = None
        self.prev_rtt = None

    def _out(self, event, rtt, dq, theta, lr, q_i):
        self.cwnd = self._clamp(self.cwnd)
        self.last_event = event
        tele = {
            "event": str(event),
            "cwnd": float(self.cwnd),
            "rtt": float(rtt),
            "min_rtt": float(self.min_rtt if self.min_rtt is not None else 0.0),
            "dq": float(dq),
            "theta": float(theta),
            "btlbw": float(self._btlbw()),
            "loss_rate": float(lr),
            "queue_share": float(q_i),
            "slow_start": int(bool(self.slow_start)),
            "md_count": int(self.n_md),
            "timeout_run": int(self.timeout_run),
        }
        return {"cwnd": float(self.cwnd), "telemetry": tele}

    # ------------------------------------------------------------------ tick
    def tick(self, obs):
        obs = obs or {}
        t = self._as_int(obs.get("t"), self._t + 1)
        self._t = t
        acked = max(0.0, self._as_float(obs.get("acked"), 0.0))
        lost = max(0.0, self._as_float(obs.get("lost"), 0.0))
        rtt = self._as_float(obs.get("rtt"), 0.0)
        if rtt <= 0.0:
            rtt = 0.0
        timeout = bool(obs.get("timeout", False))

        # ---- trim history ----
        keep = max(60.0, 20.0 * (self.min_rtt or rtt or 1.0))
        while self.hist and self.hist[0][0] < t - keep:
            self.hist.popleft()

        # ---- outage / timeout handling ----
        if timeout and acked <= 0.0:
            self.timeout_run += 1
        else:
            self.timeout_run = 0
        ref = self.last_rtt or self.min_rtt or 0.0
        limit = (1.5 * ref + 2.0) if (self.established and ref > 0.0) else 50.0
        if self.timeout_run >= limit:
            self.hist.append((t, acked, lost, -1.0))
            self.cwnd = self.min_cwnd
            self.slow_start = True
            self.last_md_t = t
            self.md_rtt = max(1.0, ref)
            self.loss_ignore_until = t + self.md_rtt
            self.timeout_run = 0
            self.n_md += 1
            self._reset_estimates()
            return self._out("timeout", rtt, 0.0, 0.0, 0.0, 0.0)

        if acked <= 0.0 or rtt <= 0.0:
            self.hist.append((t, acked, lost, -1.0))
            return self._out("noack", rtt, 0.0, 0.0, 0.0, 0.0)

        self.established = True
        event = "hold"

        # ---- base-RTT step detection ----
        path_change = False
        if self.min_rtt is not None and self.last_rtt is not None:
            jump = rtt - self.last_rtt
            prev_jump = (self.last_rtt - self.prev_rtt) if self.prev_rtt is not None else 0.0
            thr = max(3.0, 0.5 * self.min_rtt)
            if jump > thr and abs(prev_jump) < 0.3 * jump:
                path_change = True
        self.prev_rtt = self.last_rtt
        self.last_rtt = rtt

        if path_change:
            self.rtt_window.clear()
            self.dq_window.clear()
            self.min_rtt = rtt
            bw = self._btlbw()
            if bw > 0.0:
                self.cwnd = self._clamp(max(self.cwnd, 0.9 * bw * rtt))
            self.last_md_t = t
            self.md_rtt = max(1.0, rtt)
            event = "path_change"

        # ---- windowed minimum RTT ----
        base = self.min_rtt if self.min_rtt is not None else rtt
        w_min = max(40.0, 16.0 * base)
        self._push_min(self.rtt_window, t, rtt)
        self._expire(self.rtt_window, t, w_min)
        self.min_rtt = self.rtt_window[0][1]

        dq = max(0.0, rtt - self.min_rtt)
        theta = self.target_frac * self.min_rtt
        self.hist.append((t, acked, lost, dq))

        self._push_max(self.dq_window, t, dq)
        self._expire(self.dq_window, t, w_min)
        dq_max = self.dq_window[0][1]

        rate = self._rate_sample(t, rtt)
        self._push_max(self.rate_window, t, rate)
        self._expire(self.rate_window, t, w_min)
        btlbw = self._btlbw()

        q_i = self.cwnd * dq / rtt
        can_md = (t - self.last_md_t) >= self.md_rtt
        lr, lost_in_win = self._loss_rate(t, rtt)

        high_loss = lr > self.loss_rate_thresh and lost_in_win >= 2.0
        loss_at_full = (
            lost > 0.0
            and t > self.loss_ignore_until
            and dq_max > 0.5 * theta
            and dq >= 0.8 * dq_max
        )

        if path_change:
            pass
        elif high_loss or loss_at_full:
            if can_md:
                factor = self.beta_loss if high_loss else self.beta_delay
                self._md(t, rtt, factor, "loss")
                self.loss_ignore_until = t + max(1.0, rtt)
                self.slow_start = False
                event = "md_loss" if high_loss else "md_loss_full"
            else:
                event = "hold_loss"
        elif self.slow_start:
            if dq > 0.5 * theta:
                self.slow_start = False
                target = btlbw * self.min_rtt if btlbw > 0.0 else self.cwnd * self.beta_delay
                new = min(self.cwnd, max(target, 0.5 * self.cwnd))
                self.cwnd = self._clamp(new)
                self.last_md_t = t
                self.md_rtt = max(1.0, rtt)
                self.n_md += 1
                event = "ss_exit"
            else:
                self.cwnd = self._clamp(self.cwnd * (2.0 ** (1.0 / rtt)))
                event = "slow_start"
        elif dq > theta:
            if can_md and q_i > self.q_floor:
                slope = max(0.0, self._dq_slope(t, dq, rtt))
                factor = min(self.beta_delay, 1.0 / (1.0 + slope))
                factor = max(0.3, factor)
                self._md(t, rtt, factor, "delay")
                event = "md_delay"
            else:
                event = "hold_delay"
        elif dq < 0.25 * theta:
            grow = self.cwnd * ((1.0 + self.mi_gain) ** (1.0 / rtt))
            grow += self.ai_base * (1.0 + self.ai_boost) / rtt
            self.cwnd = self._clamp(grow)
            event = "mi"
        else:
            a = self.ai_base * (1.0 + self.ai_boost * (1.0 - dq / theta))
            self.cwnd = self._clamp(self.cwnd + a / rtt)
            event = "ai"

        return self._out(event, rtt, dq, theta, lr, q_i)
