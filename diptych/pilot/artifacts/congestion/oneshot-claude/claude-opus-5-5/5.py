import math
from collections import deque


class Controller:
    """Delay-based (Vegas-like) congestion controller with loss-rate safety net.

    Main ideas:
    - Keep about `alpha` of our own packets in the bottleneck queue, using a
      proportional controller. Every flow aims for the same queue share, so
      flows using this algorithm converge to equal rates.
    - Treat isolated losses as random when the flow is alone on the link and
      the loss rate is low. This keeps throughput up on lossy paths.
    - When the loss rate is high (sustained, and also high over a short
      window), halve the window once per RTT.
    - When sharing the link, treat losses that arrive with a nearly full queue
      as congestion: shrink alpha and cut the window if needed.
    - On a timeout, drop to min_cwnd and re-enter slow start.
    - Detect a jump in base RTT (a sudden step followed by a stable RTT that
      cutting the window does not lower) and re-baseline min_rtt.
    """

    def __init__(self, config: dict):
        self.min_cwnd = float(config.get("min_cwnd", 1.0))
        self.max_cwnd = float(config.get("max_cwnd", 2000.0))
        self.cwnd = float(config.get("initial_cwnd", 4.0))
        self.cwnd = min(self.max_cwnd, max(self.min_cwnd, self.cwnd))

        self.in_ss = True
        self.ssthresh = self.max_cwnd

        self.minrtt = None
        self.srtt = None
        self.rtt_hist = deque()  # (t, rtt) for recent ticks
        self.last_rtt = None
        self.last_q = 0.0

        self.qmax = 0.0
        self.rate = 0.0
        self.cest = 0.0

        self.Ls = 0.0
        self.Ns = 0.0
        self.Ll = 0.0
        self.Nl = 0.0

        self.alpha0 = 5.0
        self.alpha = self.alpha0

        self.last_cut = -1e18
        self.jump_t = -1e18
        self.streak = None
        self.n = 0
        self.event = "init"

    def _clamp(self):
        if not (self.cwnd == self.cwnd) or math.isinf(self.cwnd):
            self.cwnd = self.min_cwnd
        self.cwnd = min(self.max_cwnd, max(self.min_cwnd, self.cwnd))

    def _out(self, diff=0.0, share=1.0, lr_s=0.0, lr_l=0.0):
        self._clamp()
        return {
            "cwnd": float(self.cwnd),
            "telemetry": {
                "cwnd": float(self.cwnd),
                "minrtt": float(self.minrtt) if self.minrtt is not None else 0.0,
                "srtt": float(self.srtt) if self.srtt is not None else 0.0,
                "diff": float(diff),
                "alpha": float(self.alpha),
                "share": float(share),
                "lr_short": float(lr_s),
                "lr_long": float(lr_l),
                "slow_start": int(self.in_ss),
                "event": self.event,
            },
        }

    def tick(self, obs: dict) -> dict:
        self.n += 1
        t = obs.get("t", self.n)
        try:
            t = float(t)
        except Exception:
            t = float(self.n)
        acked = float(obs.get("acked", 0.0) or 0.0)
        lost = float(obs.get("lost", 0.0) or 0.0)
        rtt = obs.get("rtt", 0.0)
        try:
            rtt = float(rtt)
        except Exception:
            rtt = 0.0
        if not (rtt == rtt) or math.isinf(rtt):
            rtt = 0.0
        timeout = bool(obs.get("timeout", False))
        acked = max(0.0, acked)
        lost = max(0.0, lost)
        self.event = "none"

        # Timeout: full backoff.
        if timeout:
            self.ssthresh = max(2.0, self.cwnd / 2.0)
            self.cwnd = self.min_cwnd
            self.in_ss = True
            self.last_cut = t
            self.streak = None
            self.event = "timeout"
            return self._out()

        valid = rtt > 0.0 and acked > 0.0

        if valid:
            if self.srtt is None:
                self.srtt = rtt
            else:
                self.srtt += (rtt - self.srtt) / 8.0
            R0 = max(1.0, self.srtt)
            # Detect a sudden step in RTT (base RTT change signature).
            if self.minrtt is not None and self.rtt_hist:
                recent = [r for (tt, r) in self.rtt_hist if tt >= t - 2]
                if recent:
                    ref = min(recent)
                    if rtt - ref > max(0.3 * self.minrtt, 2.0):
                        self.jump_t = t
            self.rtt_hist.append((t, rtt))
            horizon = 2.0 * R0 + 2.0
            while self.rtt_hist and self.rtt_hist[0][0] < t - horizon:
                self.rtt_hist.popleft()
            if self.minrtt is None or rtt < self.minrtt:
                self.minrtt = rtt
            self.last_rtt = rtt

        R = max(1.0, self.srtt if self.srtt is not None else 1.0)

        # Loss-rate estimates (short ~1 RTT, long ~8 RTT).
        ds = 1.0 - 1.0 / max(1.0, R)
        dl = 1.0 - 1.0 / max(1.0, 8.0 * R)
        self.Ls = self.Ls * ds + lost
        self.Ns = self.Ns * ds + acked + lost
        self.Ll = self.Ll * dl + lost
        self.Nl = self.Nl * dl + acked + lost
        lr_s = self.Ls / self.Ns if self.Ns > 1e-9 else 0.0
        lr_l = self.Ll / self.Nl if self.Nl > 1e-9 else 0.0

        # Delivery rate and capacity estimate.
        self.rate += (acked - self.rate) / R
        self.cest = max(self.rate, self.cest * (1.0 - 1.0 / (40.0 * R)))
        share = self.rate / self.cest if self.cest > 1e-9 else 1.0

        if not valid or self.minrtt is None:
            # No usable sample; hold (but honor high loss).
            if lost > 0 and lr_s > 0.05 and lr_l > 0.03 and self.Nl >= 20 \
                    and t - self.last_cut >= R:
                self.cwnd *= 0.5
                self.in_ss = False
                self.last_cut = t
                self.event = "loss_rate_cut"
            return self._out(0.0, share, lr_s, lr_l)

        q = max(0.0, rtt - self.minrtt)
        self.last_q = q
        diff = self.cwnd * q / rtt
        self.qmax = max(q, self.qmax * (1.0 - 1.0 / (30.0 * R)))

        # Slowly restore alpha.
        self.alpha = min(self.alpha0, self.alpha + 0.01 * self.alpha0 / R)

        can_cut = (t - self.last_cut) >= R
        cut = False
        if lost > 0 and can_cut:
            if lr_s > 0.05 and lr_l > 0.03 and self.Nl >= 20:
                self.cwnd *= 0.5
                self.in_ss = False
                cut = True
                self.event = "loss_rate_cut"
            elif share < 0.85 and self.qmax > 0 and q >= 0.6 * self.qmax:
                # Shared link, queue near its observed max: congestion loss.
                self.alpha = max(1.0, self.alpha * 0.75)
                if diff >= 1.5 * self.alpha or self.in_ss:
                    self.cwnd *= 0.7
                    self.in_ss = False
                    self.event = "congestion_cut"
                else:
                    self.event = "alpha_cut"
                cut = True

        if cut:
            self.last_cut = t
            self.streak = None
            return self._out(diff, share, lr_s, lr_l)

        if self.in_ss:
            if diff > self.alpha:
                self.in_ss = False
                self.cwnd = max(self.min_cwnd,
                                self.cwnd * self.minrtt / rtt + self.alpha)
                self.event = "ss_exit_delay"
            elif self.cwnd >= self.ssthresh:
                self.in_ss = False
                self.event = "ss_exit_thresh"
            else:
                self.cwnd += acked
                self.event = "slow_start"
            return self._out(diff, share, lr_s, lr_l)

        # Proportional queue-share control (per RTT, spread across ticks).
        a = self.alpha
        delta = 0.3 * (a - diff)
        delta = max(delta, -0.3 * self.cwnd)
        if diff < 0.5 * a:
            # Queue essentially empty: spare capacity, probe multiplicatively.
            delta += 0.35 * self.cwnd * (1.0 - 2.0 * diff / a)
        self.cwnd += delta / R
        self.event = "track"

        # Base-RTT increase detection.
        if diff > 2.0 * a:
            if self.streak is None:
                self.streak = {"t0": t, "cwnd0": self.cwnd}
            else:
                el = t - self.streak["t0"]
                cw0 = self.streak["cwnd0"]
                recent = [r for (tt, r) in self.rtt_hist if tt >= t - R]
                if recent:
                    lo, hi = min(recent), max(recent)
                    stable = (hi - lo) <= 0.03 * hi
                else:
                    lo, stable = rtt, False
                trig = False
                if stable and el >= 2.0 * R and self.cwnd <= 0.8 * cw0 \
                        and self.jump_t >= self.streak["t0"] - 2.0 * R:
                    trig = True
                elif stable and el >= 8.0 * R and self.cwnd <= 0.5 * cw0:
                    trig = True
                if trig:
                    self.minrtt = lo
                    self.streak = None
                    self.qmax = 0.0
                    self.jump_t = -1e18
                    self.event = "base_rtt_shift"
        else:
            self.streak = None

        return self._out(diff, share, lr_s, lr_l)
