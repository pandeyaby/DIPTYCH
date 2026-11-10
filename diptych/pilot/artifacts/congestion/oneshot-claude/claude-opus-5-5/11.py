import math
from collections import deque


class Controller:
    """Delay-based congestion controller with loss classification.

    - Targets a small standing queue (~T packets of its own) using a proportional
      Vegas-style controller, so it keeps delay low when alone and yields to
      loss-based flows.
    - Losses are treated as congestive only when they come with queueing delay
      or a high loss rate. Isolated random losses at low delay are ignored, which
      keeps throughput up on lossy paths.
    - Periodic short drain probes keep the windowed minimum RTT accurate, which
      prevents the base-RTT estimate from drifting upward and lets the controller
      adapt when the path changes.
    - A timeout collapses the window to min_cwnd. A high loss rate halves it once
      per RTT.
    """

    def __init__(self, config: dict):
        self.min_cwnd = float(config.get("min_cwnd", 1.0))
        self.max_cwnd = float(config.get("max_cwnd", 2000.0))
        self.cwnd = float(config.get("initial_cwnd", 4.0))
        self.cwnd = min(self.max_cwnd, max(self.min_cwnd, self.cwnd))

        self.ss = True
        self.ssthresh = self.max_cwnd

        self.srtt = None
        self.min_q = deque()  # monotonic (t, rtt) for windowed min
        self.max_q = deque()  # monotonic (t, rtt) for windowed max

        self.T = 3.0  # target own queued packets
        self.gain = 0.5  # proportional gain per RTT

        self.g0 = 0.05
        self.g = self.g0
        self.g_max = 0.5
        self.in_fast = False
        self.g_next = 0.0

        self.last_cut = -1e18
        self.L = 0.0
        self.N = 0.0

        self.next_probe = None
        self.probe_end = -1.0
        self.freeze_until = -1.0

        self.timeouts = 0
        self.mode = "ss"

    def _clamp(self, w):
        if w != w:  # NaN guard
            w = self.min_cwnd
        return min(self.max_cwnd, max(self.min_cwnd, w))

    def _out(self, w, extra):
        w = self._clamp(w)
        tel = {
            "cwnd": float(w),
            "internal_cwnd": float(self.cwnd),
            "mode": self.mode,
            "srtt": float(self.srtt if self.srtt is not None else 0.0),
            "timeouts": int(self.timeouts),
        }
        tel.update(extra)
        return {"cwnd": float(w), "telemetry": tel}

    def tick(self, obs: dict) -> dict:
        t = float(obs.get("t", 0))
        acked = max(0.0, float(obs.get("acked", 0.0) or 0.0))
        lost = max(0.0, float(obs.get("lost", 0.0) or 0.0))
        rtt = float(obs.get("rtt", 0.0) or 0.0)
        timeout = bool(obs.get("timeout", False))

        # Timeout: full backoff.
        if timeout:
            self.timeouts += 1
            if t - self.last_cut >= (self.srtt or 1.0):
                self.ssthresh = max(2.0, self.cwnd / 2.0)
            self.cwnd = self.min_cwnd
            self.ss = True
            self.g = self.g0
            self.in_fast = False
            self.last_cut = t
            self.freeze_until = -1.0
            self.mode = "timeout"
            return self._out(self.cwnd, {"loss_rate": 0.0, "diff": 0.0})

        valid = rtt > 0.0 and acked > 0.0
        if valid:
            self.timeouts = 0
            if self.srtt is None:
                self.srtt = rtt
            else:
                self.srtt += 0.125 * (rtt - self.srtt)
            while self.min_q and self.min_q[-1][1] >= rtt:
                self.min_q.pop()
            self.min_q.append((t, rtt))
            while self.max_q and self.max_q[-1][1] <= rtt:
                self.max_q.pop()
            self.max_q.append((t, rtt))

        if self.srtt is None:
            self.srtt = rtt if rtt > 0 else 1.0
        srtt = max(1.0, self.srtt)

        # Expire window entries, always keeping the most recent sample.
        W = max(10.0, 13.0 * srtt)
        while len(self.min_q) > 1 and t - self.min_q[0][0] > W:
            self.min_q.popleft()
        while len(self.max_q) > 1 and t - self.max_q[0][0] > W:
            self.max_q.popleft()

        minr = self.min_q[0][1] if self.min_q else srtt
        maxr = self.max_q[0][1] if self.max_q else srtt

        # Loss-rate EWMA over roughly 4 RTTs.
        d = math.exp(-1.0 / (4.0 * srtt))
        self.L = self.L * d + lost
        self.N = self.N * d + acked + lost
        rate = self.L / self.N if self.N > 0 else 0.0
        high_loss = self.N > 10.0 and rate > 0.05

        r = rtt if valid else max(srtt, minr)
        if r < minr:
            r = minr
        qd = r - minr
        diff = self.cwnd * qd / r if r > 0 else 0.0
        spread = maxr - minr

        congestive = False
        if lost > 0:
            congestive = (
                high_loss
                or diff > 2.0 * self.T
                or (spread > 0.1 * minr and qd >= 0.6 * spread)
            )
            if congestive and t - self.last_cut >= srtt:
                self.cwnd = self._clamp(self.cwnd * 0.5)
                self.ssthresh = max(2.0, self.cwnd)
                self.ss = False
                self.g = self.g0
                self.in_fast = False
                self.last_cut = t
                self.freeze_until = max(self.freeze_until, t + srtt)
                self.mode = "cut"

        frozen = t < self.freeze_until

        if not frozen and acked > 0 and self.cwnd > 0:
            if self.ss:
                if diff > self.T or self.cwnd >= self.ssthresh:
                    self.ss = False
                else:
                    self.cwnd += acked
                    self.mode = "ss"
            if not self.ss:
                if diff < self.T / 2.0:
                    if not self.in_fast:
                        self.in_fast = True
                        self.g = self.g0
                        self.g_next = t + srtt
                    elif t >= self.g_next:
                        self.g = min(self.g_max, self.g * 1.5)
                        self.g_next = t + srtt
                    inc = max(self.g * acked, acked / self.cwnd)
                    self.cwnd += inc
                    self.mode = "fast"
                else:
                    self.in_fast = False
                    self.g = self.g0
                    delta = self.gain * (self.T - diff) * acked / self.cwnd
                    delta = max(delta, -0.1 * acked)
                    delta = min(delta, acked / self.cwnd)
                    self.cwnd += delta
                    self.mode = "track"

        self.cwnd = self._clamp(self.cwnd)

        # Drain probes keep the minimum-RTT estimate honest.
        out_w = self.cwnd
        probing = False
        if not self.ss:
            if self.next_probe is None:
                self.next_probe = t + 6.0 * srtt
            elif t >= self.next_probe and t >= self.probe_end:
                dur = max(1.0, math.ceil(minr))
                self.probe_end = t + dur
                self.freeze_until = max(self.freeze_until, self.probe_end + srtt)
                self.next_probe = t + 6.0 * srtt
            if t < self.probe_end:
                probing = True
                out_w = max(self.min_cwnd, 0.75 * self.cwnd)
                self.mode = "probe"

        return self._out(
            out_w,
            {
                "loss_rate": float(rate),
                "diff": float(diff),
                "min_rtt": float(minr),
                "max_rtt": float(maxr),
                "probing": int(probing),
                "congestive": int(congestive),
                "ss": int(self.ss),
            },
        )
