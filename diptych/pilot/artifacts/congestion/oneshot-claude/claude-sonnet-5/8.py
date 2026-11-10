import math
from collections import deque


class Controller:
    """Delay-aware AIMD congestion controller.

    Combines Reno-style loss-based AIMD (for compatibility/fairness with
    standard flows and for strong backoff under heavy/sustained loss) with
    a Vegas-style queueing-delay signal (estimated packet backlog) that lets
    the flow back off *before* the queue overflows, ignore isolated/random
    losses that are not accompanied by a delay signal, and quickly cede
    bandwidth when a new flow starts sharing the bottleneck.
    """

    # Vegas-style backlog thresholds, in estimated queued packets.
    _ALPHA = 2.0   # below: safe to grow
    _BETA = 6.0    # above: back off (loss or delay triggered)
    _GAMMA = 4.0   # above, during slow start: stop doubling early

    _LOSS_RATE_THRESH = 0.05  # >=5% recent loss -> treat as real congestion
    _SOFT_DECAY = 0.85        # gentle multiplicative decrease (delay-only)
    _HARD_DECAY = 0.5         # standard AIMD halving (loss + congestion)

    def __init__(self, config: dict):
        self.min_cwnd = float(config.get("min_cwnd", 1.0))
        self.max_cwnd = float(config.get("max_cwnd", 2000.0))
        initial = float(config.get("initial_cwnd", 4.0))
        self.cwnd = min(max(initial, self.min_cwnd), self.max_cwnd)

        self.ssthresh = self.max_cwnd
        self.slow_start = True

        self.min_rtt = 0.0
        self._rtt_samples = deque()   # (t, rtt)
        self._loss_samples = deque()  # (t, acked, lost)

        self.last_decrease_t = -1.0e18

    # -- internal helpers --------------------------------------------------

    def _update_min_rtt(self, t, rtt):
        self._rtt_samples.append((t, rtt))
        window = max(100.0, 20.0 * self.min_rtt)
        while self._rtt_samples and (t - self._rtt_samples[0][0]) > window:
            self._rtt_samples.popleft()
        self.min_rtt = min(v for _, v in self._rtt_samples)

    def _update_loss_window(self, t, acked, lost):
        self._loss_samples.append((t, acked, lost))
        window = max(20.0, 2.0 * self.min_rtt)
        while self._loss_samples and (t - self._loss_samples[0][0]) > window:
            self._loss_samples.popleft()

    def _recent_loss_rate(self):
        acked_sum = 0.0
        lost_sum = 0.0
        for _, a, l in self._loss_samples:
            acked_sum += a
            lost_sum += l
        denom = acked_sum + lost_sum
        return lost_sum / denom if denom > 0 else 0.0

    def _clamp(self):
        self.cwnd = min(max(self.cwnd, self.min_cwnd), self.max_cwnd)

    # -- main entry point ---------------------------------------------------

    def tick(self, obs: dict) -> dict:
        t = obs["t"]
        acked = float(obs.get("acked", 0.0))
        lost = float(obs.get("lost", 0.0))
        rtt = float(obs.get("rtt", 1.0))
        timeout = bool(obs.get("timeout", False))

        if rtt <= 0:
            rtt = 1.0

        if timeout:
            # Full backoff: nothing is getting through, collapse the window.
            self.ssthresh = max(self.min_cwnd, self.cwnd / 2.0)
            self.cwnd = self.min_cwnd
            self.slow_start = True
            self.last_decrease_t = t
            self._clamp()
            return {
                "cwnd": self.cwnd,
                "telemetry": {
                    "phase": "timeout",
                    "min_rtt": self.min_rtt,
                    "diff": 0.0,
                    "recent_loss_rate": self._recent_loss_rate(),
                    "ssthresh": self.ssthresh,
                },
            }

        self._update_min_rtt(t, rtt)
        self._update_loss_window(t, acked, lost)
        min_rtt = self.min_rtt
        recent_loss_rate = self._recent_loss_rate()

        # Estimated backlog, in packets (Vegas-style Diff).
        diff = self.cwnd * (rtt - min_rtt) / rtt if rtt > 0 else 0.0

        congestion_loss = lost > 0 and (
            recent_loss_rate > self._LOSS_RATE_THRESH or diff > self._BETA
        )
        soft_congestion = (not congestion_loss) and diff > self._BETA

        rtt_ticks = max(1.0, round(min_rtt)) if min_rtt > 0 else 1.0
        can_decrease = (t - self.last_decrease_t) >= rtt_ticks

        phase = "congestion_avoidance"

        if congestion_loss:
            phase = "congestion_loss"
            if can_decrease:
                self.cwnd = max(self.min_cwnd, self.cwnd * self._HARD_DECAY)
                self.last_decrease_t = t
            self.ssthresh = self.cwnd
            self.slow_start = False
        elif soft_congestion:
            phase = "soft_congestion"
            if can_decrease:
                self.cwnd = max(self.min_cwnd, self.cwnd * self._SOFT_DECAY)
                self.last_decrease_t = t
            self.ssthresh = min(self.ssthresh, self.cwnd)
            self.slow_start = False
        else:
            # Growth path: no signal, or an isolated/random loss we ignore.
            if self.slow_start:
                phase = "slow_start"
                self.cwnd += acked
                if self.cwnd >= self.ssthresh or diff > self._GAMMA:
                    self.slow_start = False
                    self.ssthresh = self.cwnd
            else:
                denom = max(self.cwnd, 1e-6)
                if diff < self._ALPHA:
                    self.cwnd += acked / denom
                # else: diff in [ALPHA, BETA] -> hold steady (plateau)

        self._clamp()

        return {
            "cwnd": self.cwnd,
            "telemetry": {
                "phase": phase,
                "min_rtt": min_rtt,
                "diff": diff,
                "recent_loss_rate": recent_loss_rate,
                "ssthresh": self.ssthresh,
            },
        }
