import math


class Controller:
    def __init__(self, config: dict):
        self.min_cwnd = float(config.get("min_cwnd", 1.0))
        self.max_cwnd = float(config.get("max_cwnd", 2000.0))
        if self.max_cwnd < self.min_cwnd:
            self.max_cwnd = self.min_cwnd

        initial = float(config.get("initial_cwnd", 4.0))
        self.cwnd = self._clamp(initial)
        self.ssthresh = self.max_cwnd

        self.base_rtt = None
        self.previous_rtt = None
        self.loss_ewma = 0.0
        self.backoff_clock = 1.0
        self.high_loss_clock = 0.0
        self.startup = True

        self.jump_reference = None
        self.jump_progress = 0.0
        self.mode = "startup"

    def _clamp(self, value):
        if not math.isfinite(value):
            value = self.min_cwnd
        return min(self.max_cwnd, max(self.min_cwnd, float(value)))

    @staticmethod
    def _nonnegative(value):
        try:
            value = float(value)
        except (TypeError, ValueError):
            return 0.0
        return value if math.isfinite(value) and value > 0.0 else 0.0

    def tick(self, obs: dict) -> dict:
        acked = self._nonnegative(obs.get("acked", 0.0))
        lost = self._nonnegative(obs.get("lost", 0.0))
        rtt = self._nonnegative(obs.get("rtt", 1.0))
        if rtt <= 0.0:
            rtt = self.base_rtt or 1.0
        timeout = bool(obs.get("timeout", False))

        if self.base_rtt is None:
            self.base_rtt = rtt
        elif rtt < self.base_rtt:
            self.base_rtt = rtt

        round_fraction = min(1.0, 1.0 / max(rtt, 1.0))
        self.backoff_clock += round_fraction

        observed = acked + lost
        loss_fraction = lost / observed if observed > 0.0 else 0.0
        if observed > 0.0:
            alpha = 1.0 - math.exp(
                -min(1.0, observed / max(self.cwnd, self.min_cwnd))
            )
            self.loss_ewma += alpha * (loss_fraction - self.loss_ewma)
        else:
            self.loss_ewma *= max(0.0, 1.0 - round_fraction)

        jump_active = self.jump_reference is not None

        if jump_active:
            stable = (
                lost == 0.0
                and rtt >= 1.4 * self.base_rtt
                and 0.85 * self.jump_reference <= rtt <= 1.15 * self.jump_reference
                and self.loss_ewma < 0.04
            )
            if stable:
                self.jump_progress += round_fraction
                if self.jump_progress >= 1.0:
                    old_base = self.base_rtt
                    self.base_rtt = min(rtt, self.jump_reference)
                    scale = self.base_rtt / max(old_base, 1e-9)
                    self.cwnd = self._clamp(self.cwnd * scale)
                    self.jump_reference = None
                    self.jump_progress = 0.0
                    jump_active = False
                    self.startup = False
                    self.mode = "path-delay-change"
            else:
                self.jump_reference = None
                self.jump_progress = 0.0
                jump_active = False

        if (
            not jump_active
            and self.previous_rtt is not None
            and self.previous_rtt <= 1.25 * self.base_rtt
            and rtt >= 1.5 * self.base_rtt
            and lost == 0.0
            and self.loss_ewma < 0.03
        ):
            self.jump_reference = rtt
            self.jump_progress = round_fraction
            jump_active = True
            self.mode = "probing-delay-change"

        queue_ratio = max(0.0, rtt / max(self.base_rtt, 1e-9) - 1.0)

        if timeout:
            old_cwnd = self.cwnd
            self.ssthresh = self._clamp(max(2.0, old_cwnd * 0.5))
            self.cwnd = self.min_cwnd
            self.startup = True
            self.backoff_clock = 0.0
            self.high_loss_clock = 0.0
            self.jump_reference = None
            self.jump_progress = 0.0
            self.mode = "timeout"
        else:
            if self.loss_ewma >= 0.08:
                self.high_loss_clock += round_fraction
            else:
                self.high_loss_clock = max(
                    0.0, self.high_loss_clock - 2.0 * round_fraction
                )

            if self.startup and acked > 0.0:
                growth_limit = max(0.0, self.ssthresh - self.cwnd)
                self.cwnd += min(acked, growth_limit)
                if self.cwnd >= self.ssthresh:
                    self.startup = False
                    self.mode = "avoidance"
            elif acked > 0.0:
                self.cwnd += acked / max(self.cwnd, 1.0)
                self.mode = "avoidance"

            high_loss = (
                self.high_loss_clock >= 0.5
                and self.loss_ewma >= 0.08
                and self.backoff_clock >= 1.0
            )

            delay_congestion = (
                not jump_active
                and queue_ratio > 0.06
                and self.backoff_clock >= 1.0
            )

            if high_loss:
                self.cwnd *= 0.65
                self.ssthresh = self._clamp(self.cwnd)
                self.startup = False
                self.backoff_clock = 0.0
                self.high_loss_clock = 0.0
                self.mode = "high-loss-backoff"
            elif delay_congestion:
                severity = min(1.0, (queue_ratio - 0.06) / 0.44)
                factor = max(0.5, 0.98 - 0.48 * severity)
                self.cwnd *= factor
                self.ssthresh = self._clamp(self.cwnd)
                self.startup = False
                self.backoff_clock = 0.0
                self.mode = "delay-backoff"
            elif jump_active:
                self.mode = "probing-delay-change"

        self.cwnd = self._clamp(self.cwnd)
        self.previous_rtt = rtt

        telemetry = {
            "mode": self.mode,
            "base_rtt": float(self.base_rtt),
            "rtt": float(rtt),
            "queue_ratio": float(queue_ratio),
            "loss_fraction": float(loss_fraction),
            "loss_ewma": float(self.loss_ewma),
            "ssthresh": float(self.ssthresh),
            "startup": int(self.startup),
        }
        return {"cwnd": float(self.cwnd), "telemetry": telemetry}
