"""Delay-aware AIMD congestion controller.

Behaviour summary
-----------------
* Slow start (double per RTT) until the first congestion signal.
* Congestion avoidance: +1 packet per RTT.
* Multiplicative decrease, at most once per RTT, on any of:
    - a loss that looks congestion-related (queue elevated or loss rate high)
      -> halve, like the standard algorithm;
    - a sustained high smoothed loss rate -> cwnd *= max(0.5, 1 - 2*lossrate);
    - queueing delay above a small target fraction of the base RTT
      -> cwnd *= min_rtt / rtt, which empties this flow's share of the queue.
* Losses seen while the queue is small and the loss rate is low are treated
  as random and ignored, so a lightly lossy path does not collapse the window.
* Timeout -> window collapses to min_cwnd and slow start restarts.
* Base RTT is a windowed minimum (so a path change is eventually relearned)
  plus a fast step detector: if the RTT jumps far above anything explainable
  by the queue and stays flat for ~2.5 RTTs, the base RTT is reset.
"""

import math


class Controller:
    def __init__(self, config):
        config = config or {}
        self.min_cwnd = float(config.get("min_cwnd", 1.0))
        self.max_cwnd = float(config.get("max_cwnd", 2000.0))
        self.cwnd = self._clamp(float(config.get("initial_cwnd", 4.0)))

        # tunables
        self.q_frac = 0.08      # queueing-delay target as fraction of base RTT
        self.lr_thresh = 0.03   # smoothed loss rate considered "high"

        # state
        self.ss = True
        self.have_rtt = False
        self.srtt = 0.0
        self.min_rtt = 0.0
        self.prev_rtt = 0.0
        self.cur_min = math.inf
        self.prev_min = math.inf
        self.cur_maxq = 0.0
        self.prev_maxq = 0.0
        self.epoch_start = 0
        self.last_reduce_t = -10 ** 9
        self.reduce_wait = 0.0
        self.lr = 0.0
        self.hi_run = 0
        self.hi_thresh = 0.0
        self.hi_min = math.inf
        self.last_t = -1

    # ------------------------------------------------------------------
    def _clamp(self, w):
        if not (w == w):  # NaN guard
            w = self.min_cwnd
        return max(self.min_cwnd, min(self.max_cwnd, w))

    def _telemetry(self, event, q, flat):
        return {
            "event": event,
            "mode": "slow_start" if self.ss else "avoidance",
            "cwnd": float(self.cwnd),
            "min_rtt": float(self.min_rtt),
            "srtt": float(self.srtt),
            "queue_delay": float(q),
            "loss_rate": float(self.lr),
            "hi_run": int(self.hi_run),
            "rtt_flat": int(flat),
        }

    # ------------------------------------------------------------------
    def tick(self, obs):
        t = int(obs.get("t", self.last_t + 1))
        self.last_t = t
        acked = max(0.0, float(obs.get("acked", 0.0) or 0.0))
        lost = max(0.0, float(obs.get("lost", 0.0) or 0.0))
        rtt = float(obs.get("rtt", 0.0) or 0.0)
        timeout = bool(obs.get("timeout", False))
        event = "none"

        # ---- timeout: full backoff -----------------------------------
        if timeout:
            self.cwnd = self.min_cwnd
            self.ss = True
            self.last_reduce_t = t
            self.reduce_wait = max(1.0, self.srtt)
            self.hi_run = 0
            return {"cwnd": self._clamp(self.cwnd),
                    "telemetry": self._telemetry("timeout", 0.0, False)}

        # ---- smoothed loss rate --------------------------------------
        total = acked + lost
        if total > 0:
            if self.srtt > 0:
                a = min(0.5, max(0.01, 1.0 / (6.0 * self.srtt)))
            else:
                a = 0.5
            self.lr += a * (lost / total - self.lr)

        # ---- RTT tracking --------------------------------------------
        q = 0.0
        flat = False
        valid = acked > 0 and rtt > 0 and math.isfinite(rtt)
        if valid:
            if not self.have_rtt:
                self.have_rtt = True
                self.srtt = rtt
                self.min_rtt = rtt
                self.prev_rtt = rtt
                self.cur_min = rtt
                self.epoch_start = t
            else:
                self.srtt += 0.2 * (rtt - self.srtt)

            epoch_len = max(40.0, 12.0 * self.srtt)
            if t - self.epoch_start >= epoch_len:
                self.prev_min = self.cur_min
                self.prev_maxq = self.cur_maxq
                self.cur_min = math.inf
                self.cur_maxq = 0.0
                self.epoch_start = t
            self.cur_min = min(self.cur_min, rtt)
            self.min_rtt = min(self.cur_min, self.prev_min)
            q = max(0.0, rtt - self.min_rtt)
            maxq_hist = max(self.cur_maxq, self.prev_maxq)
            flat = abs(rtt - self.prev_rtt) <= 0.02 * rtt
            self.prev_rtt = rtt

            # step-change detector for the base RTT
            if self.hi_run == 0:
                thr = self.min_rtt + max(0.5 * self.min_rtt, 1.5 * maxq_hist)
                if rtt > thr:
                    self.hi_run = 1
                    self.hi_thresh = thr
                    self.hi_min = rtt
            else:
                if rtt > self.hi_thresh:
                    self.hi_run += 1
                    self.hi_min = min(self.hi_min, rtt)
                else:
                    self.hi_run = 0
            if self.hi_run >= max(4.0, 2.5 * self.srtt):
                self.min_rtt = self.hi_min
                self.cur_min = self.hi_min
                self.prev_min = self.hi_min
                self.cur_maxq = 0.0
                self.prev_maxq = 0.0
                self.epoch_start = t
                self.hi_run = 0
                self.ss = True
                q = max(0.0, rtt - self.min_rtt)
                event = "rtt_reset"
            self.cur_maxq = max(self.cur_maxq, q)

        q_thresh = self.q_frac * self.min_rtt if self.have_rtt else math.inf

        # ---- multiplicative decrease, at most once per RTT -----------
        can_reduce = (t - self.last_reduce_t) >= self.reduce_wait
        reduced = False
        if can_reduce:
            cong_loss = lost > 0 and (q >= q_thresh or self.lr >= self.lr_thresh)
            suppress_delay = self.hi_run > 0 and (self.hi_run < 3 or flat)
            if cong_loss:
                self.cwnd = max(self.min_cwnd, 0.5 * self.cwnd)
                reduced = True
                event = "loss_halve"
            elif total > 0 and self.lr >= self.lr_thresh:
                f = max(0.5, 1.0 - 2.0 * self.lr)
                self.cwnd = max(self.min_cwnd, self.cwnd * f)
                reduced = True
                event = "loss_rate"
            elif valid and q >= q_thresh and not suppress_delay:
                new = self.cwnd * (self.min_rtt / rtt) - 1.0
                self.cwnd = max(self.min_cwnd, 0.25 * self.cwnd, new)
                reduced = True
                event = "delay"
        if reduced:
            self.ss = False
            self.last_reduce_t = t
            self.reduce_wait = (rtt if valid else self.srtt) + 1.0

        # ---- increase -------------------------------------------------
        if not reduced and acked > 0:
            if self.ss:
                self.cwnd += acked
            else:
                self.cwnd += acked / max(self.cwnd, 1.0)

        self.cwnd = self._clamp(self.cwnd)
        return {"cwnd": self.cwnd, "telemetry": self._telemetry(event, q, flat)}
