import math


class Controller:
    """Delay-sensitive AIMD with persistent-loss and path-change handling."""

    def __init__(self, config: dict):
        self.min_cwnd = self._number(config.get("min_cwnd", 1.0), 1.0)
        self.max_cwnd = self._number(config.get("max_cwnd", 2000.0), 2000.0)
        if self.max_cwnd < self.min_cwnd:
            self.max_cwnd = self.min_cwnd

        initial = self._number(config.get("initial_cwnd", 4.0), 4.0)
        self.cwnd = self._clamp(initial)

        self.base_rtt = None
        self.last_rtt = None
        self.loss_ewma = 0.0
        self.slow_start = True
        self.last_backoff_t = -math.inf

        self.shift_active = False
        self.shift_min = 0.0
        self.shift_max = 0.0
        self.shift_samples = 0

    @staticmethod
    def _number(value, default):
        try:
            value = float(value)
        except (TypeError, ValueError):
            return default
        return value if math.isfinite(value) else default

    def _clamp(self, value):
        return min(self.max_cwnd, max(self.min_cwnd, float(value)))

    def _telemetry(self, mode, rtt, queue_ratio):
        return {
            "mode": mode,
            "cwnd": float(self.cwnd),
            "base_rtt": float(self.base_rtt or rtt),
            "rtt": float(rtt),
            "queue_ratio": float(queue_ratio),
            "loss_ewma": float(self.loss_ewma),
            "slow_start": int(self.slow_start),
            "path_probe": int(self.shift_active),
        }

    def tick(self, obs: dict) -> dict:
        t = self._number(obs.get("t", 0), 0.0)
        acked = max(0.0, self._number(obs.get("acked", 0.0), 0.0))
        lost = max(0.0, self._number(obs.get("lost", 0.0), 0.0))
        timeout = bool(obs.get("timeout", False))

        fallback_rtt = self.last_rtt or self.base_rtt or 1.0
        rtt = max(1.0e-6, self._number(obs.get("rtt", fallback_rtt), fallback_rtt))

        total = acked + lost
        sample_loss = lost / total if total > 0.0 else 0.0
        self.loss_ewma = 0.90 * self.loss_ewma + 0.10 * sample_loss

        if self.base_rtt is None:
            self.base_rtt = rtt
        elif rtt < self.base_rtt:
            self.base_rtt = rtt
            self.shift_active = False

        previous_rtt = self.last_rtt
        self.last_rtt = rtt

        # Detect a stable, abrupt increase in propagation delay. Holding the
        # window briefly distinguishes a delay step from a growing queue.
        if not self.shift_active and previous_rtt is not None:
            abrupt = (
                previous_rtt < 1.30 * self.base_rtt
                and rtt > 1.55 * self.base_rtt
                and rtt > 1.35 * previous_rtt
                and sample_loss < 0.03
            )
            if abrupt:
                self.shift_active = True
                self.shift_min = rtt
                self.shift_max = rtt
                self.shift_samples = 1

        if self.shift_active:
            if sample_loss >= 0.03 or rtt < 1.45 * self.base_rtt:
                self.shift_active = False
            else:
                self.shift_min = min(self.shift_min, rtt)
                self.shift_max = max(self.shift_max, rtt)
                self.shift_samples += 1

                stable = self.shift_max <= 1.05 * self.shift_min
                required = max(3, int(math.ceil(0.5 * rtt)))
                if not stable:
                    self.shift_active = False
                elif self.shift_samples >= required:
                    old_base = self.base_rtt
                    self.base_rtt = self.shift_min
                    scale = self.base_rtt / max(old_base, 1.0e-6)
                    self.cwnd = self._clamp(self.cwnd * scale)
                    self.slow_start = False
                    self.shift_active = False

        queue_ratio = max(0.0, rtt / self.base_rtt - 1.0)

        if timeout:
            self.cwnd = self.min_cwnd
            self.slow_start = True
            self.last_backoff_t = t
            return {
                "cwnd": float(self.cwnd),
                "telemetry": self._telemetry("timeout", rtt, queue_ratio),
            }

        can_backoff = t - self.last_backoff_t >= max(1.0, rtt)
        congestion_loss = lost > 0.0 and queue_ratio > 0.15
        persistent_loss = self.loss_ewma > 0.06

        backed_off = False
        mode = "avoidance"

        if can_backoff and congestion_loss:
            self.cwnd *= 0.5
            self.last_backoff_t = t
            self.slow_start = False
            backed_off = True
            mode = "congestion_loss"
        elif can_backoff and persistent_loss:
            self.cwnd *= 0.70
            self.last_backoff_t = t
            self.slow_start = False
            backed_off = True
            mode = "persistent_loss"

        if not backed_off and not self.shift_active:
            target_queue = 0.08

            if self.slow_start:
                if queue_ratio >= target_queue or self.loss_ewma > 0.04:
                    self.slow_start = False
                    mode = "avoidance"
                else:
                    self.cwnd += acked
                    mode = "slow_start"

            if not self.slow_start:
                if queue_ratio > target_queue + 0.04:
                    reduction = 0.35 * (queue_ratio - target_queue) / max(rtt, 1.0)
                    reduction = min(0.12, max(0.0, reduction))
                    self.cwnd *= 1.0 - reduction
                    mode = "delay_backoff"
                elif acked > 0.0:
                    self.cwnd += acked / max(self.cwnd, 1.0)

        elif self.shift_active and not backed_off:
            mode = "path_probe"

        self.cwnd = self._clamp(self.cwnd)

        return {
            "cwnd": float(self.cwnd),
            "telemetry": self._telemetry(mode, rtt, queue_ratio),
        }
