"""Delay-led AIMD congestion controller with Reno-compatible loss response.

Design summary
--------------
* Slow start doubles per round trip and exits on queueing delay (not on the
  first random loss), landing near the measured bottleneck share.
* Congestion avoidance adds one packet per round trip.  When queueing delay
  exceeds 15% of the base RTT the window is cut proportionally to
  0.95 * cwnd * base / rtt, which drains the queue (AIMD, so flows converge).
* Losses only halve the window when the RTT sits near the highest RTT seen
  (buffer-full evidence).  Losses seen with an empty/short queue are treated
  as random.  A high measured loss rate always forces a reduction.
* If delay cuts do not bring the RTT down (path base RTT changed, or a
  loss-based flow holds the queue), the base RTT is re-learned.
* When the queue stays empty for several round trips and no loss-based
  competition was detected, the additive step accelerates to refill quickly.
* A timeout halves the window; persistent timeouts collapse it to min_cwnd.
"""

import math


class Controller:
    TH = 0.15            # queueing-delay limit as a fraction of base RTT
    SS_EXIT = 0.05       # slow-start exit delay threshold (fraction of base)
    DRAIN = 0.95         # delay cut lands at this fraction of estimated BDP
    CUT_FLOOR = 0.7      # largest single delay cut
    EMPTY = 0.02         # queue considered empty below this fraction of base
    EMPTY_RTTS = 5.0     # round trips of empty queue before accelerating
    HIGH_LOSS = 0.10     # loss fraction that always forces a reduction

    def __init__(self, config: dict):
        self.min_cwnd = float(config.get("min_cwnd", 1.0))
        self.max_cwnd = float(config.get("max_cwnd", 2000.0))
        if self.max_cwnd < self.min_cwnd:
            self.max_cwnd = self.min_cwnd
        self.cwnd = self._clamp(float(config.get("initial_cwnd", 4.0)))
        self.ssthresh = float("inf")

        self.last_t = -1
        self.rtt = 0.0                # last valid RTT sample
        self.base = float("inf")      # base (minimum) RTT estimate
        self.peak = 0.0               # slowly decaying maximum RTT
        self.rate = 0.0               # smoothed delivery rate (pkts/tick)

        self.to_count = 0
        self.loss_hold = -1.0         # earliest tick for the next loss cut
        self.delay_hold = -1.0        # earliest tick for the next delay check
        self.rtt_ref = None           # RTT at last delay cut in this episode
        self.flat = 0                 # consecutive unresponsive delay cuts
        self.hmin = 0.0
        self.hmax = 0.0
        self.ep_clean = False
        self.last_loss_t = -1e18
        self.deep_until = -1.0        # delay mode trusted until this tick
        self.compete_until = -1.0     # loss-based competition suspected
        self.empty_ticks = 0.0

        self.acc_a = 0.0
        self.acc_l = 0.0
        self.acc_start = None
        self.state = "init"

    # ------------------------------------------------------------------ utils
    def _clamp(self, w):
        if not (w == w):  # NaN guard
            w = self.min_cwnd
        return max(self.min_cwnd, min(self.max_cwnd, w))

    @staticmethod
    def _num(x, default=0.0):
        try:
            v = float(x)
        except (TypeError, ValueError):
            return default
        if not math.isfinite(v):
            return default
        return v

    def _reset_acc(self, t):
        self.acc_a = 0.0
        self.acc_l = 0.0
        self.acc_start = t

    def _end_episode(self):
        self.rtt_ref = None
        self.flat = 0
        self.ep_clean = False

    def _after_loss_cut(self, t, rtt, cw_before):
        self.cwnd = self._clamp(self.cwnd)
        self.ssthresh = self.cwnd
        self.loss_hold = t + rtt
        self.delay_hold = t + 2.0 * rtt
        self._end_episode()
        self.deep_until = -1.0
        self.compete_until = t + max(50.0, cw_before) * rtt
        self.empty_ticks = 0.0
        self._reset_acc(t)

    def _delay_cut(self, t, rtt):
        factor = max(self.CUT_FLOOR, self.DRAIN * self.base / rtt)
        self.cwnd = self._clamp(self.cwnd * min(1.0, factor))
        self.ssthresh = self.cwnd
        self.delay_hold = t + 2.0 * rtt
        self.loss_hold = max(self.loss_hold, t + rtt)
        self.empty_ticks = 0.0
        self._reset_acc(t)

    def _out(self):
        self.cwnd = self._clamp(self.cwnd)
        base = self.base if math.isfinite(self.base) else 0.0
        ssth = self.ssthresh if math.isfinite(self.ssthresh) else -1.0
        return {
            "cwnd": float(self.cwnd),
            "telemetry": {
                "state": self.state,
                "base_rtt": float(base),
                "peak_rtt": float(self.peak),
                "rtt": float(self.rtt),
                "ssthresh": float(ssth),
                "rate": float(self.rate),
                "deep": 1 if self.last_t < self.deep_until else 0,
                "compete": 1 if self.last_t < self.compete_until else 0,
                "flat": int(self.flat),
            },
        }

    # ------------------------------------------------------------------- tick
    def tick(self, obs: dict) -> dict:
        t = self._num(obs.get("t", self.last_t + 1), self.last_t + 1)
        self.last_t = t
        acked = max(0.0, self._num(obs.get("acked", 0.0)))
        lost = max(0.0, self._num(obs.get("lost", 0.0)))
        rtt_obs = self._num(obs.get("rtt", 0.0))
        timeout = bool(obs.get("timeout", False))

        # ------------------------------------------------------------ timeout
        if timeout:
            self.to_count += 1
            r = self.rtt if self.rtt > 0 else 1.0
            if self.to_count == 1:
                self.ssthresh = max(2.0 * self.min_cwnd, self.cwnd / 2.0)
                self.cwnd = self._clamp(self.cwnd / 2.0)
            else:
                self.cwnd = self.min_cwnd
            self._end_episode()
            self.deep_until = -1.0
            self.compete_until = t + 100.0 * r
            self.loss_hold = t + r
            self.delay_hold = t + 2.0 * r
            self.empty_ticks = 0.0
            self.last_loss_t = t
            self._reset_acc(t)
            self.state = "timeout"
            return self._out()
        self.to_count = 0

        if lost > 0 and not (acked > 0 and rtt_obs > 0):
            self.last_loss_t = t
        if not (acked > 0 and rtt_obs > 0):
            self.state = "idle"
            return self._out()

        # ------------------------------------------------------- measurements
        rtt = rtt_obs
        self.rtt = rtt
        if rtt < self.base:
            self.base = rtt
        base = self.base
        if self.peak > base:
            self.peak = base + (self.peak - base) * (1.0 - 1.0 / (100.0 * max(rtt, 1.0)))
        if rtt > self.peak:
            self.peak = rtt
        g = min(1.0, 3.0 / max(rtt, 1.0))
        self.rate = acked if self.rate <= 0 else self.rate + g * (acked - self.rate)
        if self.flat >= 1:
            self.hmin = min(self.hmin, rtt)
            self.hmax = max(self.hmax, rtt)

        q = rtt - base
        limit = base * (1.0 + self.TH)
        above = rtt > limit
        cut_done = False

        # ------------------------------------------- high loss rate reduction
        if self.acc_start is None:
            self.acc_start = t
        self.acc_a += acked
        self.acc_l += lost
        el = t - self.acc_start
        tot = self.acc_a + self.acc_l
        if el >= rtt and (tot >= 20.0 or el >= 4.0 * rtt):
            if self.acc_l >= 2.0 and self.acc_l > self.HIGH_LOSS * tot:
                cw = self.cwnd
                self.cwnd = cw * 0.7
                self._after_loss_cut(t, rtt, cw)
                cut_done = True
                self.state = "high_loss"
            else:
                self._reset_acc(t)

        in_ss = self.cwnd < self.ssthresh

        # ------------------------------------------------ slow start exit
        if not cut_done and in_ss and q > self.SS_EXIT * base:
            first = not math.isfinite(self.ssthresh)
            target = self.cwnd * base / rtt
            if self.rate > 0:
                target = min(target, 1.05 * self.rate * base)
            self.cwnd = self._clamp(max(0.5 * self.cwnd, target))
            self.ssthresh = self.cwnd
            self.delay_hold = t + rtt
            self.empty_ticks = 0.0
            if first:
                # No sign of a shallow buffer yet: trust delay control until
                # the first delay cut has had time to happen.
                self.deep_until = t + max(30.0, 0.3 * self.cwnd) * rtt
            in_ss = False
            cut_done = True
            self.state = "ss_exit"

        # ------------------------------------------------ delay control
        hold_increase = False
        if not cut_done and not in_ss:
            if above:
                self.empty_ticks = 0.0
                hold_increase = True
                if t >= self.delay_hold:
                    if self.rtt_ref is None:
                        self.ep_clean = (lost <= 0) and (t - self.last_loss_t) > rtt
                        self._delay_cut(t, rtt)
                        self.rtt_ref = rtt
                        self.flat = 0
                        cut_done = True
                        self.state = "delay_cut"
                    else:
                        ratio = rtt / self.rtt_ref
                        if ratio > 1.05:
                            # still building: cut again
                            self._delay_cut(t, rtt)
                            self.rtt_ref = rtt
                            self.flat = 0
                            self.ep_clean = False
                            cut_done = True
                            self.state = "delay_cut"
                        elif ratio >= 0.9:
                            self.flat += 1
                            if self.flat < 2:
                                # probe: one more cut to see if delay is ours
                                self._delay_cut(t, rtt)
                                self.rtt_ref = rtt
                                self.hmin = rtt
                                self.hmax = rtt
                                cut_done = True
                                self.state = "delay_probe"
                            else:
                                # delay does not respond to us: re-learn base
                                steady = (self.hmax - self.hmin) <= 0.01 * self.hmin
                                self.base = min(self.hmin, rtt)
                                self.peak = max(self.hmax, rtt)
                                self._end_episode()
                                self.deep_until = -1.0
                                self.delay_hold = t + rtt
                                if steady and t >= self.compete_until:
                                    self.empty_ticks = self.EMPTY_RTTS * rtt
                                cut_done = True
                                self.state = "rebase"
                        else:
                            # queue is draining: wait
                            self.rtt_ref = rtt
                            self.flat = 0
                            self.delay_hold = t + rtt
            else:
                if self.rtt_ref is not None:
                    if self.flat == 0 and (self.ep_clean or t < self.deep_until):
                        # delay cut worked: buffer is deeper than our target
                        self.deep_until = t + max(50.0, 0.5 * self.cwnd) * rtt
                        if self.ep_clean:
                            self.compete_until = -1.0
                    self._end_episode()

        # ------------------------------------------------ loss response
        if lost > 0:
            if not cut_done and t >= self.loss_hold:
                qref = max(self.peak - base, 0.4 * self.TH * base)
                cong = q >= 0.8 * qref
                if t < self.deep_until and not above:
                    cong = False
                if cong:
                    cw = self.cwnd
                    self.cwnd = cw * 0.5
                    self._after_loss_cut(t, rtt, cw)
                    cut_done = True
                    self.state = "loss_cut"
            self.last_loss_t = t

        # ------------------------------------------------ window increase
        if not cut_done and not hold_increase:
            if in_ss:
                self.cwnd += acked
                if math.isfinite(self.ssthresh) and self.cwnd > self.ssthresh:
                    self.cwnd = self.ssthresh
                self.state = "slow_start"
            else:
                if q <= self.EMPTY * base:
                    self.empty_ticks += 1.0
                else:
                    self.empty_ticks = 0.0
                a = 1.0
                self.state = "avoid"
                need = self.EMPTY_RTTS * rtt
                if self.empty_ticks > need and t >= self.compete_until:
                    k = (self.empty_ticks - need) / max(rtt, 1e-9)
                    a = max(1.0, min(2.0 ** min(k, 20.0), 0.5 * self.cwnd))
                    self.state = "probe_up"
                self.cwnd += a * acked / max(self.cwnd, 1e-9)
        elif hold_increase and not cut_done:
            self.state = "hold"

        return self._out()
