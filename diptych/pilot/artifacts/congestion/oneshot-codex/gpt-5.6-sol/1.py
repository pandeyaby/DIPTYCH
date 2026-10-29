import math


class Controller:
    def __init__(self, config: dict):
        self.min_cwnd = float(config.get("min_cwnd", 1.0))
        self.max_cwnd = float(config.get("max_cwnd", 2000.0))
        if self.max_cwnd < self.min_cwnd:
            self.max_cwnd = self.min_cwnd

        initial = float(config.get("initial_cwnd", 4.0))
        self.cwnd = self._clamp(initial)

        self.base_rtt = None
        self.last_rtt = None
        self.loss_ewma = 0.0
        self.queue_limit = None
        self.slow_start = True
        self.next_loss_cut = float("-inf")
        self.last_t = -1

        self.shift_start = None
        self.shift_old_base = None
        self.shift_min = None
        self.shift_max = None

    def _clamp(self, value):
        if not math.isfinite(value):
            value = self.min_cwnd
        return min(self.max_cwnd, max(self.min_cwnd, float(value)))

    @staticmethod
    def _number(value, default=0.0):
        try:
            value = float(value)
        except (TypeError, ValueError):
            return default
        return value if math.isfinite(value) else default

    def tick(self, obs: dict) -> dict:
        t = int(obs.get("t", self.last_t + 1))
        acked = max(0.0, self._number(obs.get("acked", 0.0)))
        lost = max(0.0, self._number(obs.get("lost", 0.0)))
        rtt = max(1.0e-6, self._number(obs.get("rtt", 1.0), 1.0))
        timeout = bool(obs.get("timeout", False))
        total = acked + lost
        loss_fraction = lost / total if total > 0.0 else 0.0

        alpha = 0.12
        self.loss_ewma += alpha * (loss_fraction - self.loss_ewma)

        event = "hold"
        shifted = False

        if self.base_rtt is None:
            self.base_rtt = rtt
        elif rtt < self.base_rtt:
            self.base_rtt = rtt
            self.shift_start = None

        abrupt = (
            self.last_rtt is not None
            and rtt > self.base_rtt * 1.45
            and rtt > self.last_rtt * 1.30
            and loss_fraction < 0.02
            and self.loss_ewma < 0.025
        )

        if abrupt and self.shift_start is None:
            self.shift_start = t
            self.shift_old_base = self.base_rtt
            self.shift_min = rtt
            self.shift_max = rtt

        if self.shift_start is not None:
            self.shift_min = min(self.shift_min, rtt)
            self.shift_max = max(self.shift_max, rtt)

            if (
                lost > 0.0
                or rtt < self.shift_old_base * 1.25
                or self.shift_max > self.shift_min * 1.20
            ):
                self.shift_start = None
            else:
                needed = max(3, int(math.ceil(self.shift_old_base)))
                if t - self.shift_start >= needed:
                    new_base = self.shift_min
                    ratio = min(2.5, max(1.0, new_base / self.shift_old_base))
                    self.base_rtt = new_base
                    self.cwnd = self._clamp(self.cwnd * ratio)
                    self.shift_start = None
                    shifted = True
                    event = "base_rtt_change"

        queue_delay = max(0.0, rtt - self.base_rtt)
        queue_ratio = queue_delay / max(self.base_rtt, 1.0e-6)

        high_loss = self.loss_ewma >= 0.06
        delay_congestion = queue_ratio >= 0.12
        congestion_loss = lost > 0.0 and (high_loss or delay_congestion)

        if congestion_loss and queue_delay > 0.0:
            if self.queue_limit is None:
                self.queue_limit = queue_delay
            else:
                self.queue_limit = min(self.queue_limit, queue_delay)

        if timeout or (lost > 0.0 and acked <= 0.0):
            self.cwnd = self._clamp(1.0)
            self.slow_start = False
            self.next_loss_cut = t + max(1.0, rtt)
            event = "timeout"
        elif congestion_loss and t >= self.next_loss_cut:
            self.cwnd = self._clamp(self.cwnd * 0.5)
            self.slow_start = False
            self.next_loss_cut = t + max(1.0, rtt)
            event = "loss_backoff"
        elif not shifted and self.shift_start is None:
            target_delay = 0.05 * self.base_rtt
            if self.queue_limit is not None:
                target_delay = min(target_delay, 0.25 * self.queue_limit)
            target_delay = max(1.0e-6, target_delay)

            round_fraction = min(
                1.0,
                acked / max(self.cwnd, self.min_cwnd, 1.0e-6),
            )

            if self.slow_start:
                if queue_delay <= target_delay and not high_loss:
                    self.cwnd = self._clamp(self.cwnd + acked)
                    event = "slow_start"
                else:
                    bdp_estimate = self.cwnd * self.base_rtt / rtt
                    self.cwnd = self._clamp(
                        min(self.cwnd, bdp_estimate * 1.03)
                    )
                    self.slow_start = False
                    event = "delay_exit"
            elif high_loss:
                if t >= self.next_loss_cut:
                    self.cwnd = self._clamp(self.cwnd * 0.5)
                    self.next_loss_cut = t + max(1.0, rtt)
                    event = "high_loss_backoff"
            elif queue_delay < target_delay:
                headroom = 1.0 - queue_delay / target_delay
                increase = round_fraction * max(0.05, headroom)
                self.cwnd = self._clamp(self.cwnd + increase)
                event = "additive_increase"
            elif queue_delay > target_delay * 1.15:
                excess = (queue_delay - target_delay) / max(
                    self.base_rtt, 1.0e-6
                )
                factor = math.exp(-1.5 * excess * round_fraction)
                self.cwnd = self._clamp(self.cwnd * factor)
                event = "delay_backoff"

        self.cwnd = self._clamp(self.cwnd)
        self.last_rtt = rtt
        self.last_t = t

        return {
            "cwnd": float(self.cwnd),
            "telemetry": {
                "phase": "slow_start" if self.slow_start else "avoidance",
                "event": event,
                "base_rtt": float(self.base_rtt),
                "queue_delay": float(queue_delay),
                "loss_ewma": float(self.loss_ewma),
                "loss_fraction": float(loss_fraction),
                "cwnd": float(self.cwnd),
            },
        }
