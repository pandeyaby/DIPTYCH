import math
from collections import deque


class Controller:
    """
    Delay-aware AIMD with random-loss tolerance.

    - Slow start: doubles each RTT. Exits on queueing delay, congestive loss or
      a high loss rate.
    - Congestion avoidance: adds `inc` packets per RTT. `inc` starts at 1 and
      doubles each RTT while the queue stays empty, so free capacity is found
      quickly. It resets to 1 as soon as a queue forms.
    - When queueing delay goes over a target (a fraction of min RTT), the window
      is cut so the queue drains. The cut is a multiplicative decrease, which
      keeps flows of this algorithm fair to each other.
    - A loss counts as congestion only when the queue is near the top of what
      the buffer has shown (buffer overflow), or well above the target delay.
      Then the window is halved. Other isolated losses are treated as random.
    - A smoothed loss rate is the safety net: above 4% the window shrinks, and
      above 10% it is halved every RTT.
    - A timeout collapses the window to min_cwnd.
    - If RTT stays flat while the window shrinks a lot, the base RTT must have
      gone up, so min_rtt is reset.
    """

    THETA = 0.2          # target queueing delay as a fraction of min_rtt
    CLEAR_FRAC = 0.05    # queue counts as "empty" below this fraction of min_rtt

    def __init__(self, config: dict):
        self.min_cwnd = float(config.get("min_cwnd", 1.0))
        self.max_cwnd = float(config.get("max_cwnd", 2000.0))
        self.cwnd = float(config.get("initial_cwnd", 4.0))
        self.cwnd = self._clamp(self.cwnd)

        self.slow_start = True
        self.ssthresh = math.inf
        self.min_rtt = None
        self.last_rtt = None
        self.last_cut_t = -10**9
        self.inc = 1.0
        self.round_start = 0
        self.round_clear = True
        self.loss_ewma = 0.0
        self.sent_ewma = 0.0
        self.qd_peak = 0.0
        self.hist = deque()
        self.last_event = "init"

    def _clamp(self, w):
        if w != w:  # NaN guard
            w = self.min_cwnd
        return max(self.min_cwnd, min(self.max_cwnd, w))

    def _out(self, qd=0.0):
        self.cwnd = self._clamp(self.cwnd)
        lr = self.loss_ewma / self.sent_ewma if self.sent_ewma > 0 else 0.0
        return {
            "cwnd": float(self.cwnd),
            "telemetry": {
                "cwnd": float(self.cwnd),
                "min_rtt": float(self.min_rtt if self.min_rtt is not None else 0.0),
                "qdelay": float(qd),
                "qd_peak": float(self.qd_peak),
                "loss_rate": float(lr),
                "inc": float(self.inc),
                "slow_start": int(self.slow_start),
                "event": self.last_event,
            },
        }

    def tick(self, obs: dict) -> dict:
        t = int(obs.get("t", 0))
        acked = float(obs.get("acked", 0.0) or 0.0)
        lost = float(obs.get("lost", 0.0) or 0.0)
        rtt = obs.get("rtt", None)
        timeout = bool(obs.get("timeout", False))
        acked = max(0.0, acked)
        lost = max(0.0, lost)

        try:
            rtt = float(rtt)
        except (TypeError, ValueError):
            rtt = None
        if rtt is None or not (rtt > 0) or rtt != rtt or math.isinf(rtt):
            rtt = self.last_rtt if self.last_rtt is not None else 1.0
        rtt = max(rtt, 1e-3)
        self.last_rtt = rtt

        if self.min_rtt is None or rtt < self.min_rtt:
            self.min_rtt = rtt

        # Smoothed loss rate over roughly 8 RTTs.
        total = acked + lost
        if total > 0:
            g = min(0.5, max(0.001, 1.0 / (8.0 * rtt)))
            if self.sent_ewma == 0.0:
                self.loss_ewma = lost
                self.sent_ewma = total
            else:
                self.loss_ewma += g * (lost - self.loss_ewma)
                self.sent_ewma += g * (total - self.sent_ewma)
        loss_rate = self.loss_ewma / self.sent_ewma if self.sent_ewma > 0 else 0.0

        # Timeout: collapse the window.
        if timeout:
            self.ssthresh = max(self.cwnd / 2.0, 2.0)
            self.cwnd = self.min_cwnd
            self.slow_start = True
            self.last_cut_t = t
            self.inc = 1.0
            self.round_start = t
            self.round_clear = False
            self.hist.clear()
            self.last_event = "timeout"
            return self._out(0.0)

        # Reset min_rtt when the base RTT went up: RTT stays flat while the
        # window shrinks a lot, so the extra delay is not from a queue.
        self.hist.append((t, rtt, self.cwnd))
        while self.hist and self.hist[0][0] < t - rtt:
            self.hist.popleft()
        target = self.THETA * self.min_rtt
        if rtt - self.min_rtt > target and len(self.hist) >= 3 \
                and (t - self.hist[0][0]) >= rtt - 1:
            rmin = min(h[1] for h in self.hist)
            rmax = max(h[1] for h in self.hist)
            cmin = min(h[2] for h in self.hist)
            cmax = max(h[2] for h in self.hist)
            if rmax <= rmin * 1.02 and cmax >= 1.2 * cmin:
                self.min_rtt = rmin
                self.qd_peak = 0.0
                self.hist.clear()
                self.last_event = "minrtt_reset"
        target = self.THETA * self.min_rtt

        qd = max(0.0, rtt - self.min_rtt)
        decay = 1.0 - 1.0 / (100.0 * rtt)
        self.qd_peak = max(qd, self.qd_peak * decay)

        # Decide whether this loss is congestive.
        congestive = False
        if lost > 0:
            buffer_limited = (qd > 0.02 * self.min_rtt
                              and qd >= 0.85 * self.qd_peak
                              and self.qd_peak < target)
            above_target = qd > 1.3 * target
            congestive = buffer_limited or above_target

        eligible = (t - self.last_cut_t) >= rtt
        factor = None
        event = None
        if eligible:
            if congestive or loss_rate > 0.10:
                factor = 0.5
                event = "loss_cut"
            elif loss_rate > 0.04:
                factor = 0.8
                event = "lossrate_cut"
            elif qd > target:
                if self.slow_start:
                    # The current window is about twice the window behind this
                    # RTT sample.
                    factor = 0.5 * 0.85 * self.min_rtt / rtt
                    factor = max(0.25, min(0.5, factor))
                    event = "ss_exit_delay"
                else:
                    factor = 0.85 * self.min_rtt / rtt
                    factor = max(0.5, min(0.85, factor))
                    event = "delay_cut"

        if factor is not None:
            self.cwnd = self._clamp(self.cwnd * factor)
            self.slow_start = False
            self.ssthresh = self.cwnd
            self.last_cut_t = t
            self.inc = 1.0
            self.round_start = t
            self.round_clear = False
            self.last_event = event
        elif not (qd > target or congestive):
            if self.slow_start:
                self.cwnd += acked
                if self.cwnd >= self.ssthresh:
                    self.slow_start = False
                    self.inc = 1.0
                    self.round_start = t
                    self.round_clear = True
                self.last_event = "slow_start"
            else:
                if qd >= self.CLEAR_FRAC * self.min_rtt:
                    self.round_clear = False
                    self.inc = 1.0
                if t - self.round_start >= rtt:
                    if self.round_clear:
                        self.inc = min(self.inc * 2.0, max(1.0, self.cwnd * 0.5))
                    else:
                        self.inc = 1.0
                    self.round_start = t
                    self.round_clear = True
                self.cwnd += self.inc * acked / max(self.cwnd, 1e-6)
                self.last_event = "avoid"
        else:
            if qd >= self.CLEAR_FRAC * self.min_rtt:
                self.round_clear = False
                self.inc = 1.0
            self.last_event = "hold"

        self.cwnd = self._clamp(self.cwnd)
        return self._out(qd)
