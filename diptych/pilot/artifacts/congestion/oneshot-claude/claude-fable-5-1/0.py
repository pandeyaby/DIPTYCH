"""Delay-based congestion controller with random-loss tolerance.

Design summary
--------------
* Slow start doubles the window each round trip until queueing is observed
  (Vegas-style ``diff`` test) or a congestion loss occurs.
* Steady state is a Vegas-like controller that keeps a few packets of its own
  data queued at the bottleneck (``diff`` held in [ALPHA, BETA]); it grows
  multiplicatively (min 1 pkt/RTT) when the queue is empty and shrinks to the
  target when the queue is too deep.
* Losses are classified: a loss with a large estimated queue or a high recent
  loss rate is congestion (window halves, at most once per RTT); otherwise it
  is treated as random and ignored.  Persistent timeouts collapse the window
  to ``min_cwnd``.
* The base RTT is the minimum over a sliding window of per-round minima.  A
  periodic "drain probe" (and an "anomaly probe" triggered by an unexplained
  jump in queueing delay) briefly reduces the window so the flow's own queue
  drains.  If the RTT does not fall when it should have, the elevated RTT is
  accepted as the new base (path minimum delay changed) and the window is
  restored / re-sized from the measured delivery rate.
"""
import collections


class Controller:
    ALPHA = 2.0             # lower bound of per-flow queued packets
    BETA = 4.0              # upper bound of per-flow queued packets
    TARGET = 3.0            # where to settle after a delay-based reduction
    GROWTH = 0.05           # multiplicative growth per RTT when queue is empty
    BASE_WINDOW_ROUNDS = 40
    BW_WINDOW_ROUNDS = 40
    PROBE_PERIOD = 12       # rounds between periodic drain probes
    ANOMALY_GAP = 4         # min rounds between anomaly-triggered probes
    PROBE_FRACTION = 0.2    # fraction of cwnd removed during a probe
    LOSS_RATE_THRESH = 0.05
    LOSS_WINDOW_ROUNDS = 4
    ACCEPT_RATIO = 0.3

    def __init__(self, config):
        cfg = config or {}
        self.min_cwnd = float(cfg.get("min_cwnd", 1.0))
        self.max_cwnd = float(cfg.get("max_cwnd", 2000.0))
        if self.max_cwnd < self.min_cwnd:
            self.max_cwnd = self.min_cwnd
        self.cwnd = self._clamp(float(cfg.get("initial_cwnd", 4.0)))

        self.rtt_est = None
        self.base_rtt = None
        self.round_mins = collections.deque()   # (round_idx, min rtt)
        self.bw_hist = collections.deque()      # (round_idx, delivered/tick)
        self.bw_max = 0.0

        self.slow_start = True
        self.round_idx = 0
        self.round_start = None
        self.round_len = 1.0
        self.round_cwnd = self.cwnd
        self.prev_round_cwnd = self.cwnd
        self.r_min = None
        self.r_max = None
        self.r_acked = 0.0
        self.r_lost = 0.0

        self.hist = collections.deque()         # (t, acked, lost)
        self.h_acked = 0.0
        self.h_lost = 0.0
        self.loss_rate = 0.0

        self.last_cut_t = None
        self.last_cut_round = -1
        self.cut_in_round = False
        self.consec_timeouts = 0
        self.seen_ack = False

        self.probe = None
        self.last_probe_round = 0
        self.prev_diff = 0.0
        self.last_diff = 0.0
        self.cwnd_hist = collections.deque(maxlen=6)  # (round_idx, start cwnd)

        self.cuts = 0
        self.random_losses = 0
        self.accepts = 0
        self.collapses = 0
        self.mode = "slow_start"

    # ------------------------------------------------------------ helpers
    def _clamp(self, v):
        if v != v:  # NaN guard
            v = self.min_cwnd
        return max(self.min_cwnd, min(self.max_cwnd, float(v)))

    def _update_base(self):
        best = None
        for _, m in self.round_mins:
            if best is None or m < best:
                best = m
        if self.r_min is not None and (best is None or self.r_min < best):
            best = self.r_min
        self.base_rtt = best

    def _queue_est(self, rtt):
        if self.base_rtt is None or rtt is None or rtt <= 0:
            return 0.0
        return self.cwnd * max(0.0, rtt - self.base_rtt) / rtt

    def _reset_round(self, t):
        self.round_start = t
        self.round_len = max(1.0, float(self.rtt_est or 1.0))
        self.prev_round_cwnd = self.round_cwnd
        self.round_cwnd = self.cwnd
        self.r_min = None
        self.r_max = None
        self.r_acked = 0.0
        self.r_lost = 0.0
        self.cut_in_round = False

    # --------------------------------------------------------------- tick
    def tick(self, obs):
        t = int(obs.get("t", 0))
        acked = float(obs.get("acked", 0.0) or 0.0)
        lost = float(obs.get("lost", 0.0) or 0.0)
        rtt = float(obs.get("rtt", 0.0) or 0.0)
        timeout = bool(obs.get("timeout", False))
        acked = max(0.0, acked)
        lost = max(0.0, lost)

        # --- sliding-window loss rate
        self.hist.append((t, acked, lost))
        self.h_acked += acked
        self.h_lost += lost
        win = max(8.0, self.LOSS_WINDOW_ROUNDS * float(self.rtt_est or 8.0))
        while self.hist and self.hist[0][0] < t - win:
            _, a0, l0 = self.hist.popleft()
            self.h_acked -= a0
            self.h_lost -= l0
        tot = self.h_acked + self.h_lost
        self.loss_rate = (self.h_lost / tot) if tot > 1e-12 else 0.0

        # --- RTT sample
        if acked > 0 and rtt > 0:
            self.seen_ack = True
            self.rtt_est = rtt
            if self.round_start is None:
                self._reset_round(t)
            if self.r_min is None or rtt < self.r_min:
                self.r_min = rtt
            if self.r_max is None or rtt > self.r_max:
                self.r_max = rtt
            self._update_base()
        self.r_acked += acked
        self.r_lost += lost

        # --- persistent timeout -> full backoff
        if timeout and acked <= 0:
            self.consec_timeouts += 1
        else:
            self.consec_timeouts = 0
        if self.seen_ack and self.rtt_est:
            limit = 2.0 * self.rtt_est + 2.0
        else:
            limit = 64.0
        collapsed = False
        if self.consec_timeouts >= limit:
            self.cwnd = self.min_cwnd
            self.slow_start = True
            self.probe = None
            self.last_probe_round = self.round_idx
            self.last_cut_t = t
            self.last_cut_round = self.round_idx + 1
            self.cut_in_round = True
            self.consec_timeouts = 0
            self.collapses += 1
            collapsed = True

        # --- loss handling
        if lost > 0 and not collapsed:
            self._on_loss(t, rtt if rtt > 0 else (self.rtt_est or 0.0))

        # --- end of round
        if self.round_start is not None and t - self.round_start >= self.round_len:
            self._end_round(t)

        self.cwnd = self._clamp(self.cwnd)
        if self.probe is not None:
            self.mode = "probe"
        elif self.slow_start:
            self.mode = "slow_start"
        else:
            self.mode = "steady"
        telemetry = {
            "cwnd": float(self.cwnd),
            "base_rtt": float(self.base_rtt) if self.base_rtt is not None else 0.0,
            "rtt": float(rtt),
            "diff": float(self.last_diff),
            "loss_rate": float(self.loss_rate),
            "bw_max": float(self.bw_max),
            "mode": self.mode,
            "probe_phase": int(self.probe["phase"]) if self.probe else 0,
            "round": int(self.round_idx),
            "cuts": int(self.cuts),
            "random_losses": int(self.random_losses),
            "base_accepts": int(self.accepts),
            "collapses": int(self.collapses),
        }
        return {"cwnd": float(self.cwnd), "telemetry": telemetry}

    # --------------------------------------------------------------- loss
    def _on_loss(self, t, rtt):
        gap = float(self.rtt_est or 1.0)
        if self.last_cut_t is not None and t - self.last_cut_t < gap:
            return
        q = self._queue_est(rtt)
        congestion = (self.loss_rate > self.LOSS_RATE_THRESH) or (q > 2.0 * self.BETA)
        if not congestion:
            self.random_losses += 1
            return
        ref = self.cwnd
        if self.probe is not None:
            ref = max(ref, self.probe["pre_cwnd"])
            self.probe = None
            self.last_probe_round = self.round_idx
        self.cwnd = self._clamp(0.5 * ref)
        self.slow_start = False
        self.last_cut_t = t
        self.last_cut_round = self.round_idx + 1
        self.cut_in_round = True
        self.cuts += 1

    # -------------------------------------------------------------- round
    def _end_round(self, t):
        start_cwnd = self.round_cwnd
        dur = max(1.0, float(t - self.round_start))
        bw = self.r_acked / dur
        self.round_idx += 1

        self.bw_hist.append((self.round_idx, bw))
        while self.bw_hist and self.bw_hist[0][0] <= self.round_idx - self.BW_WINDOW_ROUNDS:
            self.bw_hist.popleft()
        self.bw_max = max(b for _, b in self.bw_hist) if self.bw_hist else 0.0

        r_min = self.r_min
        r_max = self.r_max
        if r_min is not None:
            self.round_mins.append((self.round_idx, r_min))
        while self.round_mins and self.round_mins[0][0] <= self.round_idx - self.BASE_WINDOW_ROUNDS:
            self.round_mins.popleft()
        self.cwnd_hist.append((self.round_idx, start_cwnd))

        if r_min is not None and r_min > 0:
            # base computed from window of round minima (current round now stored)
            saved = self.r_min
            self.r_min = None
            self._update_base()
            self.r_min = saved
            if self.base_rtt is not None:
                diff = self.cwnd * max(0.0, r_min - self.base_rtt) / r_min
                diff_hi = self.cwnd * max(0.0, r_max - self.base_rtt) / r_max if r_max else diff
                self._control(diff, diff_hi, r_min)
                self.prev_diff = diff
                self.last_diff = diff

        self._reset_round(t)

    def _control(self, diff, diff_hi, r_min):
        if self.cut_in_round:
            return
        if self.probe is not None:
            self._probe_step(diff, r_min)
            return
        if self.slow_start:
            if diff_hi > self.BETA:
                self.slow_start = False
                self.cwnd = self._clamp(max(self.cwnd - diff_hi + self.TARGET, 0.5 * self.cwnd))
                self.last_probe_round = self.round_idx
            else:
                self.cwnd = self._clamp(self.cwnd * 2.0)
            return

        growth = max(0.0, self.round_cwnd - self.prev_round_cwnd)
        anomaly = diff > self.BETA and (diff - self.prev_diff) > 2.0 * growth + 1.0
        since = self.round_idx - self.last_probe_round
        if (anomaly and since >= self.ANOMALY_GAP) or since >= self.PROBE_PERIOD:
            if self._start_probe(diff, r_min):
                return
        if diff > self.BETA:
            self.cwnd = self._clamp(max(self.cwnd - diff + self.TARGET, 0.5 * self.cwnd))
        elif diff < self.ALPHA:
            self.cwnd = self._clamp(self.cwnd + max(1.0, self.GROWTH * self.cwnd))

    # -------------------------------------------------------------- probe
    def _start_probe(self, diff, r_min):
        reduce = max(diff + 2.0, self.PROBE_FRACTION * self.cwnd)
        reduce = min(reduce, self.cwnd - self.min_cwnd)
        self.last_probe_round = self.round_idx
        if reduce <= 0.0:
            return False
        self.probe = {
            "phase": 1,
            "pre_cwnd": self.cwnd,
            "pre_rtt": r_min,
            "pre_diff": diff,
            "base0": self.base_rtt,
            "reduce": reduce,
            "min": float("inf"),
        }
        self.cwnd = self._clamp(self.cwnd - reduce)
        return True

    def _probe_step(self, diff, r_min):
        p = self.probe
        if r_min < p["min"]:
            p["min"] = r_min
        if p["phase"] == 1:
            p["phase"] = 2
            self.cwnd = self._clamp(max(self.cwnd, p["pre_cwnd"]))
            return
        self.probe = None
        self.last_probe_round = self.round_idx
        if p["pre_diff"] <= self.BETA:
            return
        expected = min(p["pre_rtt"] - (p["base0"] if p["base0"] is not None else p["pre_rtt"]),
                       p["reduce"] / max(self.bw_max, 1e-9))
        observed = p["pre_rtt"] - p["min"]
        if p["reduce"] >= 4.0 and expected > 0.0 and observed < self.ACCEPT_RATIO * expected:
            # RTT did not fall although our queue was drained: base RTT changed.
            self.base_rtt = p["min"]
            self.round_mins.clear()
            self.round_mins.append((self.round_idx, p["min"]))
            cands = [c for (ri, c) in self.cwnd_hist if ri > self.last_cut_round]
            restore = max(cands) if cands else self.cwnd
            jump = self.bw_max * self.base_rtt + self.TARGET
            self.cwnd = self._clamp(max(self.cwnd, restore, min(2.0 * self.cwnd, jump)))
            self.accepts += 1
        else:
            if diff > self.BETA:
                self.cwnd = self._clamp(max(self.cwnd - diff + self.TARGET, 0.5 * self.cwnd))
