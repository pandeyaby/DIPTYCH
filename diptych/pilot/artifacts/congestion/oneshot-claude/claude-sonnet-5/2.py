import copy


class Controller:
    """Delay-aware congestion control with a loss-rate backstop.

    Behaves like TCP Vegas (targets a small, roughly constant backlog of
    packets in the bottleneck queue) during normal operation, but falls back
    to a Reno-style multiplicative decrease when the observed loss rate over
    a round trip is high enough to indicate real congestion (as opposed to
    random, non-congestive loss). Timeouts collapse the window immediately.
    """

    def __init__(self, config: dict):
        self.min_cwnd = float(config.get("min_cwnd", 1.0))
        self.max_cwnd = float(config.get("max_cwnd", 2000.0))
        self.cwnd = float(config.get("initial_cwnd", 4.0))
        self.cwnd = max(self.min_cwnd, min(self.max_cwnd, self.cwnd))

        self.slow_start = True

        self.high_loss_thresh = 0.08
        self.alpha = 2.0
        self.beta = 4.0

        self.rtt_samples = []
        self.min_rtt = None

        self.round_start_t = None
        self.round_acked = 0.0
        self.round_lost = 0.0
        self.round_rtt_sum = 0.0
        self.round_rtt_count = 0

        self.last_loss_frac = 0.0
        self.last_diff = 0.0

    def _update_min_rtt(self, t, rtt):
        self.rtt_samples.append((t, rtt))
        window = max(200.0, 20.0 * rtt)
        cutoff = t - window
        while self.rtt_samples and self.rtt_samples[0][0] < cutoff:
            self.rtt_samples.pop(0)
        self.min_rtt = min(r for _, r in self.rtt_samples)

    def _clamp(self):
        self.cwnd = max(self.min_cwnd, min(self.max_cwnd, self.cwnd))

    def tick(self, obs: dict) -> dict:
        t = obs["t"]
        acked = float(obs.get("acked", 0.0))
        lost = float(obs.get("lost", 0.0))
        rtt = float(obs.get("rtt", 1.0))
        rtt = max(rtt, 1e-6)
        timeout = bool(obs.get("timeout", False))

        if timeout:
            self.cwnd = self.min_cwnd
            self.slow_start = True
            self.round_start_t = t
            self.round_acked = 0.0
            self.round_lost = 0.0
            self.round_rtt_sum = 0.0
            self.round_rtt_count = 0
            self.last_loss_frac = 1.0
            self._clamp()
            return {
                "cwnd": self.cwnd,
                "telemetry": {
                    "phase": "timeout",
                    "min_rtt": self.min_rtt if self.min_rtt is not None else rtt,
                    "loss_frac": self.last_loss_frac,
                    "diff": self.last_diff,
                },
            }

        if self.round_start_t is None:
            self.round_start_t = t

        self._update_min_rtt(t, rtt)

        self.round_acked += acked
        self.round_lost += lost
        self.round_rtt_sum += rtt
        self.round_rtt_count += 1

        elapsed = t - self.round_start_t

        if elapsed >= max(1.0, rtt):
            total = self.round_acked + self.round_lost
            loss_frac = (self.round_lost / total) if total > 0 else 0.0
            avg_rtt = (
                self.round_rtt_sum / self.round_rtt_count
                if self.round_rtt_count
                else rtt
            )
            min_rtt = self.min_rtt if self.min_rtt else avg_rtt
            min_rtt = max(min_rtt, 1e-6)

            high_loss = loss_frac > self.high_loss_thresh
            expected = self.cwnd / min_rtt
            actual = self.cwnd / max(avg_rtt, 1e-6)
            diff = max(0.0, (expected - actual) * min_rtt)

            if self.slow_start:
                if high_loss:
                    self.cwnd = self.cwnd * 0.5
                    self.slow_start = False
                elif diff > self.beta:
                    self.slow_start = False
                else:
                    self.cwnd = self.cwnd * 2.0
            else:
                if high_loss:
                    self.cwnd = self.cwnd * 0.5
                elif diff > self.beta:
                    self.cwnd = self.cwnd - 1.0
                elif diff < self.alpha:
                    self.cwnd = self.cwnd + 1.0

            self._clamp()

            self.last_loss_frac = loss_frac
            self.last_diff = diff

            self.round_start_t = t
            self.round_acked = 0.0
            self.round_lost = 0.0
            self.round_rtt_sum = 0.0
            self.round_rtt_count = 0

        self._clamp()

        return {
            "cwnd": self.cwnd,
            "telemetry": {
                "phase": "slow_start" if self.slow_start else "cong_avoid",
                "min_rtt": self.min_rtt if self.min_rtt is not None else rtt,
                "loss_frac": self.last_loss_frac,
                "diff": self.last_diff,
            },
        }
