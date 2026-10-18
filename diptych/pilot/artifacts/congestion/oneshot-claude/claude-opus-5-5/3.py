import math


class Controller:
    """Delay-based congestion controller (FAST/Vegas style) with a loss-rate guard.

    - Keeps a small, fixed number of its own packets queued at the bottleneck
      (ALPHA), so the standing queue stays tiny and random losses are ignored.
    - Slow-starts (doubling per RTT) whenever the queue is observed empty.
    - Cuts the window multiplicatively only when the loss *rate* over a round
      is high (well above random-loss levels), and collapses on timeouts.
    - Re-learns the base RTT if the RTT stays perfectly flat while the window
      is being cut (queue is empty, so the path's minimum delay has changed).
    """

    ALPHA = 3.0          # target packets of ours in the bottleneck queue
    GAMMA = 0.5          # FAST smoothing gain per RTT
    SS_GAIN = 0.7        # ~doubling per RTT
    SS_STEP_CAP = 0.15   # max fractional growth per tick in slow start
    LOSS_THRESH = 0.07   # loss rate per round considered "high"
    LOSS_BETA = 0.7      # multiplicative cut on high loss
    ROUND_MIN_PKTS = 30.0
    FLAT_TOL = 2e-4
    FLAT_LOSS_MAX = 0.03

    def __init__(self, config: dict):
        self.min_cwnd = float(config.get("min_cwnd", 1.0))
        self.max_cwnd = float(config.get("max_cwnd", 2000.0))
        self.cwnd = self._clamp(float(config.get("initial_cwnd", 4.0)))

        self.base = None
        self.last_rtt = None
        self.ss = True
        self.hold = False
        self.low_ticks = 0
        self.to_count = 0

        # loss-rate round
        self.r_ticks = 0
        self.r_lost = 0.0
        self.r_tot = 0.0

        # flat-RTT run (base RTT change detection)
        self.f_ref = None
        self.f_ticks = 0
        self.f_cwnd0 = self.cwnd
        self.f_lost = 0.0
        self.f_tot = 0.0

        self.q_est = 0.0
        self.loss_rate = 0.0

    # ------------------------------------------------------------------ utils
    def _clamp(self, w):
        if not (w == w) or math.isinf(w):
            w = self.min_cwnd
        return max(self.min_cwnd, min(self.max_cwnd, w))

    @staticmethod
    def _num(x, default=0.0):
        try:
            v = float(x)
        except (TypeError, ValueError):
            return default
        if v != v or math.isinf(v):
            return default
        return v

    def _out(self, mode):
        self.cwnd = self._clamp(self.cwnd)
        return {
            "cwnd": float(self.cwnd),
            "telemetry": {
                "mode": mode,
                "base_rtt": float(self.base) if self.base is not None else 0.0,
                "q_est": float(self.q_est),
                "loss_rate": float(self.loss_rate),
                "hold": int(self.hold),
                "slow_start": int(self.ss),
            },
        }

    # ------------------------------------------------------------------- tick
    def tick(self, obs: dict) -> dict:
        acked = max(0.0, self._num(obs.get("acked", 0.0)))
        lost = max(0.0, self._num(obs.get("lost", 0.0)))
        rtt = self._num(obs.get("rtt", 0.0))
        timeout = bool(obs.get("timeout", False)) or (acked <= 0.0 and lost > 0.0)

        rtt_valid = rtt > 0.0
        if not rtt_valid:
            rtt = self.last_rtt if self.last_rtt is not None else 1.0

        # ---- timeouts: full backoff -------------------------------------
        if timeout:
            self.to_count += 1
            if self.to_count >= 2:
                self.cwnd = self.min_cwnd
            else:
                self.cwnd = self.cwnd * 0.5
            self.ss = True
            self.hold = False
            self.low_ticks = 0
            self.r_ticks = 0
            self.r_lost = 0.0
            self.r_tot = 0.0
            self.f_ref = None
            return self._out("timeout")
        self.to_count = 0

        if acked <= 0.0:
            # nothing delivered and nothing lost: no information this tick
            return self._out("idle")

        if rtt_valid:
            self.last_rtt = rtt
            if self.base is None or rtt < self.base:
                self.base = rtt
        if self.base is None:
            self.base = rtt

        frac = min(1.0, 1.0 / rtt)  # fraction of an RTT covered by this tick

        # ---- base RTT change detection (flat RTT while window shrinks) ----
        if self.f_ref is None or abs(rtt - self.f_ref) > self.FLAT_TOL * self.f_ref:
            self.f_ref = rtt
            self.f_ticks = 0
            self.f_cwnd0 = self.cwnd
            self.f_lost = 0.0
            self.f_tot = 0.0
        else:
            self.f_ticks += 1
        self.f_lost += lost
        self.f_tot += acked + lost
        if (
            self.f_ticks >= 4
            and self.f_ticks >= 2.0 * rtt
            and rtt > self.base * 1.01
            and self.cwnd <= 0.85 * self.f_cwnd0
            and self.f_lost <= self.FLAT_LOSS_MAX * self.f_tot
        ):
            # The RTT did not respond at all to a substantial cut of our
            # window and the link is not overflowing: the queue is empty and
            # the path's minimum delay has gone up.
            self.base = rtt
            self.ss = True
            self.low_ticks = 0
            self.f_ticks = 0
            self.f_cwnd0 = self.cwnd
            self.f_lost = 0.0
            self.f_tot = 0.0

        # ---- loss-rate round ---------------------------------------------
        self.r_ticks += 1
        self.r_lost += lost
        self.r_tot += acked + lost
        if self.r_ticks >= rtt and (
            self.r_tot >= self.ROUND_MIN_PKTS or self.r_ticks >= 8.0 * rtt
        ):
            self.loss_rate = self.r_lost / self.r_tot if self.r_tot > 0 else 0.0
            if self.loss_rate > self.LOSS_THRESH:
                self.cwnd = self._clamp(self.cwnd * self.LOSS_BETA)
                self.hold = True
                self.ss = False
                self.low_ticks = 0
            else:
                self.hold = False
            self.r_ticks = 0
            self.r_lost = 0.0
            self.r_tot = 0.0

        # ---- delay-based window control ----------------------------------
        qd = max(0.0, rtt - self.base)
        q = self.cwnd * qd / rtt  # our packets sitting in the queue
        self.q_est = q
        target = (self.base / rtt) * self.cwnd + self.ALPHA

        if self.hold:
            # high loss: only decreases are allowed
            if target < self.cwnd:
                self.cwnd += self.GAMMA * frac * (target - self.cwnd)
            return self._out("loss_hold")

        if self.ss:
            if q >= 0.5 * self.ALPHA:
                self.ss = False
                self.low_ticks = 0
            else:
                self.cwnd += self.cwnd * min(self.SS_GAIN * frac, self.SS_STEP_CAP)
                return self._out("slow_start")

        self.cwnd += self.GAMMA * frac * (target - self.cwnd)

        if q < 0.25 * self.ALPHA:
            self.low_ticks += 1
            if self.low_ticks > rtt + 1.0:
                # queue has been empty for a full round trip: probe quickly
                self.ss = True
                self.low_ticks = 0
        else:
            self.low_ticks = 0

        return self._out("delay")
