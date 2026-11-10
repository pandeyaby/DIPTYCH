import math
from collections import deque


class Controller:
    """
    Delay-based AIMD controller with loss classification.

    - The window grows while the measured queueing delay is low and shrinks
      multiplicatively, at most once per RTT, when queueing delay passes a
      small threshold. This keeps the queue short and lets competing flows
      converge to a fair share.
    - A loss counts as congestion only when the queue is clearly building,
      the smoothed loss rate is high, or nothing got through. Isolated
      random losses at low queue are ignored.
    - On a timeout the window drops to min_cwnd (full backoff).
    - A detector notices a jump in the path's base RTT and re-learns it.
    """

    DELAY_BETA = 0.8
    LOSS_BETA = 0.5
    THRESH_FRAC = 0.2
    LOSS_RATE_LIMIT = 0.05
    VEL_CAP = 16.0

    def __init__(self, config: dict):
        self.min_cwnd = float(config.get("min_cwnd", 1.0))
        self.max_cwnd = float(config.get("max_cwnd", 2000.0))
        self.cwnd = self._clamp(float(config.get("initial_cwnd", 4.0)))

        self.in_ss = True
        self.ssthresh = float("inf")

        self.srtt = None
        self.last_rtt = None
        self.minq = deque()
        self.base = None
        self.q = 0.0
        self.maxq = 0.0

        self.t_last_dec = -1e18
        self.vel = 1.0

        self.ep_start = None
        self.ep_acked = 0.0
        self.ep_lost = 0.0
        self.ep_maxq = 0.0
        self.ep_dec = False

        self.lossrate = 0.0

        self.streak = 0
        self.samples = []
        self.dipped = True

        self.noack = 0
        self.last_event = "init"

    def _clamp(self, w):
        if w != w:
            w = self.min_cwnd
        return max(self.min_cwnd, min(self.max_cwnd, w))

    def _out(self):
        self.cwnd = self._clamp(self.cwnd)
        return {
            "cwnd": float(self.cwnd),
            "telemetry": {
                "cwnd": float(self.cwnd),
                "base_rtt": float(self.base) if self.base is not None else -1.0,
                "srtt": float(self.srtt) if self.srtt is not None else -1.0,
                "qdelay": float(self.q),
                "loss_rate": float(self.lossrate),
                "vel": float(self.vel),
                "slow_start": int(self.in_ss),
                "event": self.last_event,
            },
        }

    def _reset_streak(self):
        self.streak = 0
        self.samples = []

    def tick(self, obs: dict) -> dict:
        t = int(obs.get("t", 0))
        acked = float(obs.get("acked", 0.0) or 0.0)
        lost = float(obs.get("lost", 0.0) or 0.0)
        rtt = obs.get("rtt", None)
        timeout = bool(obs.get("timeout", False))
        self.last_event = "none"

        if self.ep_start is None:
            self.ep_start = t

        # RTT / base-RTT / queueing-delay estimation
        valid = (
            rtt is not None
            and isinstance(rtt, (int, float))
            and math.isfinite(rtt)
            and rtt > 0
            and not timeout
        )
        if valid:
            rtt = float(rtt)
            self.last_rtt = rtt
            if self.srtt is None:
                self.srtt = rtt
            else:
                self.srtt += (rtt - self.srtt) / 8.0
            ref = self.base if self.base is not None else rtt
            window = max(50.0, 20.0 * ref)
            while self.minq and self.minq[-1][1] >= rtt:
                self.minq.pop()
            self.minq.append((t, rtt))
            while len(self.minq) > 1 and self.minq[0][0] < t - window:
                self.minq.popleft()
            self.base = self.minq[0][1]
            self.q = max(0.0, rtt - self.base)

        srtt = self.srtt if self.srtt is not None else 1.0
        rtt_now = self.last_rtt if self.last_rtt is not None else 1.0
        if self.base is not None:
            thresh = max(self.THRESH_FRAC * self.base, 0.1)
        else:
            thresh = float("inf")
        q = self.q

        # Decaying maximum of queueing delay (for loss classification)
        decay = 1.0 - 1.0 / max(1.0, 10.0 * srtt)
        self.maxq = max(q, self.maxq * decay)

        # Epoch accounting
        self.ep_acked += acked
        self.ep_lost += lost
        self.ep_maxq = max(self.ep_maxq, q)
        if q <= thresh:
            self.dipped = True

        # Timeout: full backoff
        if timeout:
            self.ssthresh = max(self.cwnd / 2.0, 2.0)
            self.cwnd = self.min_cwnd
            self.in_ss = True
            self.vel = 1.0
            self.t_last_dec = t
            self.ep_dec = True
            self._reset_streak()
            self.last_event = "timeout"
            self._end_epoch(t, srtt, thresh)
            return self._out()

        # Persistent "nothing gets through" without an explicit timeout flag
        if acked <= 0 and lost > 0:
            self.noack += 1
        elif acked > 0:
            self.noack = 0
        if self.noack >= max(2.0, srtt):
            self.cwnd = self.min_cwnd
            self.ssthresh = max(2.0, self.ssthresh / 2.0) if math.isfinite(self.ssthresh) else 2.0
            self.in_ss = False
            self.vel = 1.0
            self.t_last_dec = t
            self.ep_dec = True
            self._reset_streak()
            self.last_event = "no_progress"
            self._end_epoch(t, srtt, thresh)
            return self._out()

        can_dec = (t - self.t_last_dec) >= max(1.0, rtt_now)

        # Classify congestion signals
        loss_cong = lost > 0 and (
            q > max(2.0 * thresh, 0.5 * self.maxq)
            or self.lossrate > self.LOSS_RATE_LIMIT
            or acked <= 0
        )
        if self.in_ss:
            delay_cong = q > 0.5 * thresh
        else:
            delay_cong = q > thresh

        if loss_cong:
            if can_dec:
                self.cwnd = self._clamp(self.cwnd * self.LOSS_BETA)
                self.ssthresh = self.cwnd
                self.in_ss = False
                self.t_last_dec = t
                self.vel = 1.0
                self.ep_dec = True
                self._reset_streak()
                self.last_event = "loss_dec"
            else:
                self.last_event = "loss_hold"
        elif delay_cong:
            if can_dec:
                if self.in_ss:
                    self.in_ss = False
                    self.cwnd = self._clamp(self.cwnd * 0.5)
                    self.ssthresh = self.cwnd
                    self._reset_streak()
                    self.last_event = "ss_exit"
                else:
                    self.cwnd = self._clamp(self.cwnd * self.DELAY_BETA)
                    self.last_event = "delay_dec"
                    # Base-RTT change detector: repeated decreases with the
                    # RTT stuck flat means the path's base delay rose.
                    if self.dipped:
                        self._reset_streak()
                    self.dipped = False
                    self.streak += 1
                    self.samples.append(rtt_now)
                    if len(self.samples) > 8:
                        self.samples = self.samples[-8:]
                    if self.streak >= 4:
                        last3 = self.samples[-3:]
                        if max(last3) - min(last3) < 0.1 * thresh:
                            newbase = min(last3)
                            self.minq = deque([(t, newbase)])
                            self.base = newbase
                            self.q = max(0.0, rtt_now - newbase)
                            self.maxq = self.q
                            self.in_ss = True
                            self.ssthresh = float("inf")
                            self._reset_streak()
                            self.dipped = True
                            self.last_event = "base_reset"
                self.t_last_dec = t
                self.vel = 1.0
                self.ep_dec = True
            else:
                self.last_event = "delay_hold"
        else:
            if self.in_ss:
                self.cwnd = self._clamp(self.cwnd + acked)
                if self.cwnd >= self.ssthresh:
                    self.in_ss = False
                self.last_event = "ss_grow"
            else:
                inc = self.vel * acked / max(self.cwnd, 1.0)
                self.cwnd = self._clamp(self.cwnd + inc)
                self.last_event = "grow"

        self._end_epoch(t, srtt, thresh)
        return self._out()

    def _end_epoch(self, t, srtt, thresh):
        if t - self.ep_start + 1 < max(1.0, srtt):
            return
        tot = self.ep_acked + self.ep_lost
        if tot > 0:
            inst = self.ep_lost / tot
            self.lossrate = 0.75 * self.lossrate + 0.25 * inst
        if not self.in_ss:
            if (not self.ep_dec) and self.ep_maxq < 0.5 * thresh:
                self.vel = min(self.vel * 2.0, self.VEL_CAP)
            elif self.ep_maxq >= 0.5 * thresh:
                self.vel = 1.0
        self.ep_start = t + 1
        self.ep_acked = 0.0
        self.ep_lost = 0.0
        self.ep_maxq = 0.0
        self.ep_dec = False
