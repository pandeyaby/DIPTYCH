"""Delay-led AIMD congestion controller with loss discrimination.

Design summary
--------------
* The primary congestion signal is queueing delay (rtt - base_rtt), not loss.
  The window grows additively (1 packet / RTT) while the flow's own estimated
  backlog  cwnd * (rtt - base) / rtt  is below a small target, and is cut
  multiplicatively (to ~85% of the estimated BDP) once it is above.  Additive
  increase plus a shared multiplicative decrease converges to a fair share
  between flows of this algorithm, and the queue stays far below the buffer.
* Random losses are ignored: a loss only causes a Reno-style halving when the
  queue is clearly built up (backlog well above target), or when the measured
  loss rate is high (sustained heavy loss always reduces the rate).
* Timeouts: halve on the first, collapse to min_cwnd when they persist.
* Base-RTT changes: with a fixed window a real queue always reacts to a window
  cut.  If the RTT is elevated but perfectly flat, the elevation is path delay
  rather than queue, so the base RTT is re-learned and the window is restored
  to the previously delivered rate times the new RTT.
* Against buffer-filling (Reno) flows the controller yields: it backs off on
  delay long before the buffer is full and halves on congestive loss.
"""

import math


class Controller:
    SS = "ss"
    CA = "ca"

    THETA = 0.06        # target backlog as a fraction of cwnd
    ALPHA = 2.0         # minimum target backlog, packets
    DRAIN = 0.85        # delay cut aims at this fraction of the estimated BDP
    FLAT_EPS = 1e-4     # relative RTT tolerance for "flat"

    def __init__(self, config: dict):
        config = config or {}
        self.min_cwnd = float(config.get("min_cwnd", 1.0))
        self.max_cwnd = float(config.get("max_cwnd", 2000.0))
        if self.max_cwnd < self.min_cwnd:
            self.max_cwnd = self.min_cwnd
        self.cwnd = self._clamp(float(config.get("initial_cwnd", 4.0)))

        self.mode = self.SS
        self.base = float("inf")
        self.last_rtt = 1.0
        self.last_t = -1

        self.next_cut_t = 0
        self.rtt_at_cut = None

        self.sent_acc = 0.0
        self.lost_acc = 0.0

        self.to_count = 0

        self.flat_ref = -1.0
        self.flat_count = 0

        self.rate_ewma = None
        self.rate_cur = 0.0
        self.rate_prev = 0.0
        self.rate_bucket_t = 0

        self.confirm_t = 0
        self.unconf_min = float("inf")

        self.n_delay_cuts = 0
        self.n_loss_cuts = 0
        self.n_base_resets = 0

    # ------------------------------------------------------------------ utils
    def _clamp(self, w):
        if w != w:  # NaN
            w = self.min_cwnd
        if w < self.min_cwnd:
            w = self.min_cwnd
        if w > self.max_cwnd:
            w = self.max_cwnd
        return float(w)

    @staticmethod
    def _num(x):
        try:
            x = float(x)
        except (TypeError, ValueError):
            return 0.0
        if not math.isfinite(x):
            return 0.0
        return x

    def _out(self, event, q_est=0.0):
        self.cwnd = self._clamp(self.cwnd)
        base = self.base if math.isfinite(self.base) else 0.0
        return {
            "cwnd": self.cwnd,
            "telemetry": {
                "mode": self.mode,
                "event": event,
                "base_rtt": float(base),
                "q_est": float(q_est),
                "delay_cuts": int(self.n_delay_cuts),
                "loss_cuts": int(self.n_loss_cuts),
                "base_resets": int(self.n_base_resets),
            },
        }

    # ------------------------------------------------------------------- tick
    def tick(self, obs: dict) -> dict:
        try:
            t = int(obs.get("t", self.last_t + 1))
        except (TypeError, ValueError):
            t = self.last_t + 1
        self.last_t = t
        acked = max(0.0, self._num(obs.get("acked", 0.0)))
        lost = max(0.0, self._num(obs.get("lost", 0.0)))
        rtt = self._num(obs.get("rtt", 0.0))
        timeout = bool(obs.get("timeout", False))

        # ---------------------------------------------------------- timeouts
        if timeout:
            self.to_count += 1
            if self.to_count >= 2:
                self.cwnd = self.min_cwnd
            else:
                self.cwnd = max(self.min_cwnd, self.cwnd * 0.5)
            self.mode = self.SS
            self.sent_acc = 0.0
            self.lost_acc = 0.0
            self.flat_count = 0
            self.rtt_at_cut = None
            self.next_cut_t = t + max(1, int(math.ceil(self.last_rtt)))
            return self._out("timeout")

        if acked > 0.0:
            self.to_count = 0

        valid = acked > 0.0 and rtt > 0.0
        r = rtt if valid else self.last_rtt

        # ---------------------------------------------------- loss accounting
        tau = max(2.0 * r, 16.0)
        decay = 1.0 - 1.0 / tau
        self.sent_acc = self.sent_acc * decay + acked + lost
        self.lost_acc = self.lost_acc * decay + lost

        if not valid:
            return self._out("idle")

        self.last_rtt = rtt
        rtt_ticks = max(1, int(math.ceil(rtt)))
        eps = self.FLAT_EPS * rtt

        # ------------------------------------------------- delivered-rate max
        if self.rate_ewma is None:
            self.rate_ewma = acked
            self.rate_bucket_t = t
        else:
            self.rate_ewma += (acked - self.rate_ewma) / max(2.0, min(rtt, 50.0))
        if t - self.rate_bucket_t >= max(40, 8 * rtt_ticks):
            self.rate_prev = self.rate_cur
            self.rate_cur = self.rate_ewma
            self.rate_bucket_t = t
        if self.rate_ewma > self.rate_cur:
            self.rate_cur = self.rate_ewma
        rate_max = max(self.rate_cur, self.rate_prev)

        # ------------------------------------------------------ base RTT
        if rtt < self.base:
            self.base = rtt
        if rtt <= self.base * 1.02:
            self.confirm_t = t
            self.unconf_min = float("inf")
        else:
            if rtt < self.unconf_min:
                self.unconf_min = rtt
            # Slow fallback: base not seen for a very long time.
            if t - self.confirm_t > 100.0 + rtt * max(40.0, 0.3 * self.cwnd):
                self.base = self.unconf_min
                self.confirm_t = t
                self.unconf_min = float("inf")

        # ------------------------------------------- flat-RTT (path change)
        if abs(rtt - self.flat_ref) <= eps:
            self.flat_count += 1
        else:
            self.flat_ref = rtt
            self.flat_count = 1

        event = "none"
        dq = rtt - self.base
        if dq < 0.0:
            dq = 0.0
        if self.flat_count >= max(4, rtt_ticks + 2) and dq > 5.0 * eps:
            # Elevated but perfectly steady RTT: path delay, not a queue.
            self.base = rtt
            self.confirm_t = t
            self.unconf_min = float("inf")
            cand = 0.95 * rate_max * rtt
            if cand > self.cwnd:
                self.cwnd = min(cand, self.max_cwnd)
            self.mode = self.CA
            self.next_cut_t = t + rtt_ticks
            self.rtt_at_cut = None
            self.n_base_resets += 1
            dq = 0.0
            event = "base_reset"

        cwnd = self.cwnd
        q_est = cwnd * dq / rtt
        target = max(self.ALPHA, self.THETA * cwnd)
        guard_ok = t >= self.next_cut_t

        high_loss = self.lost_acc >= 0.04 * self.sent_acc + 1.5
        congestive_loss = lost > 0.0 and q_est > 2.0 * target

        if event == "base_reset":
            pass
        elif high_loss and guard_ok:
            cwnd *= 0.5
            self.mode = self.CA
            self.sent_acc = 0.0
            self.lost_acc = 0.0
            self.next_cut_t = t + rtt_ticks
            self.rtt_at_cut = None
            self.n_loss_cuts += 1
            event = "high_loss_cut"
        elif congestive_loss and guard_ok:
            cwnd *= 0.5
            self.mode = self.CA
            self.next_cut_t = t + rtt_ticks
            self.rtt_at_cut = None
            self.n_loss_cuts += 1
            event = "loss_cut"
        elif self.mode == self.SS:
            if q_est > max(1.5, 0.03 * cwnd):
                cwnd *= max(0.5, 0.95 * self.base / rtt)
                self.mode = self.CA
                self.next_cut_t = t + rtt_ticks
                self.rtt_at_cut = rtt
                self.n_delay_cuts += 1
                event = "ss_exit"
            else:
                cwnd += 0.7 * acked
                event = "ss_grow"
        elif q_est > target:
            bypass = (self.rtt_at_cut is not None
                      and rtt > self.rtt_at_cut * (1.0 + self.THETA))
            if guard_ok or bypass:
                if (not bypass and self.rtt_at_cut is not None
                        and abs(rtt - self.rtt_at_cut) <= eps):
                    # The previous cut had no effect on delay: wait for the
                    # flat detector instead of collapsing the window.
                    event = "hold"
                else:
                    cwnd *= max(0.5, self.DRAIN * self.base / rtt)
                    self.next_cut_t = t + rtt_ticks
                    self.rtt_at_cut = rtt
                    self.n_delay_cuts += 1
                    event = "delay_cut"
            else:
                event = "wait"
        else:
            self.rtt_at_cut = None
            cwnd += acked / max(cwnd, 1.0)
            event = "ai"

        self.cwnd = self._clamp(cwnd)
        return self._out(event, q_est)
