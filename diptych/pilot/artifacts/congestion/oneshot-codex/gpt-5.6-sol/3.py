import math
from collections import deque


class Controller:
    def __init__(self, config: dict):
        self.min_cwnd = self._number(config.get("min_cwnd", 1.0), 1.0)
        self.max_cwnd = self._number(config.get("max_cwnd", 2000.0), 2000.0)
        if self.max_cwnd < self.min_cwnd:
            self.max_cwnd = self.min_cwnd

        initial = self._number(config.get("initial_cwnd", 4.0), 4.0)
        self.cwnd = self._clamp(initial)

        self.base_rtt = None
        self.previous_rtt = None
        self.rtt_samples = deque()

        self.slow_start = True
        self.last_delay_reduce = -10**18
        self.last_loss_reduce = -10**18
        self.zero_ack_ticks = 0

        self.loss_mass = 0.0
        self.packet_mass = 0.0
        self.high_loss_ticks = 0

        self.shift_candidate = None
        self.shift_started = None
        self.last_t = -1

    @staticmethod
    def _number(value, default):
        try:
            value = float(value)
        except (TypeError, ValueError):
            return default
        return value if math.isfinite(value) else default

    def _clamp(self, value):
        if not math.isfinite(value):
            value = self.min_cwnd
        return min(self.max_cwnd, max(self.min_cwnd, float(value)))

    def _update_base_rtt(self, t, rtt, loss_rate):
        if self.base_rtt is None:
            self.base_rtt = rtt
            self.rtt_samples.append((t, rtt))
            self.previous_rtt = rtt
            return

        abrupt_jump = (
            self.previous_rtt is not None
            and self.previous_rtt <= 1.25 * self.base_rtt
            and rtt >= 1.55 * self.base_rtt
        )

        if abrupt_jump and loss_rate < 0.03:
            self.shift_candidate = rtt
            self.shift_started = t
        elif self.shift_candidate is not None:
            stable = 0.82 * self.shift_candidate <= rtt <= 1.18 * self.shift_candidate
            if not stable or loss_rate >= 0.05:
                self.shift_candidate = None
                self.shift_started = None
            else:
                self.shift_candidate = min(self.shift_candidate, rtt)
                required = max(3.0, 2.0 * self.shift_candidate)
                if t - self.shift_started >= required:
                    self.base_rtt = self.shift_candidate
                    self.rtt_samples.clear()
                    self.rtt_samples.append((t, rtt))
                    self.shift_candidate = None
                    self.shift_started = None

        self.rtt_samples.append((t, rtt))
        horizon = max(64, int(32.0 * rtt))
        while self.rtt_samples and t - self.rtt_samples[0][0] > horizon:
            self.rtt_samples.popleft()

        if self.rtt_samples:
            measured_min = min(sample for _, sample in self.rtt_samples)
            self.base_rtt = min(self.base_rtt, measured_min)

        self.previous_rtt = rtt

    def tick(self, obs: dict) -> dict:
        t = int(obs.get("t", self.last_t + 1))
        acked = max(0.0, self._number(obs.get("acked", 0.0), 0.0))
        lost = max(0.0, self._number(obs.get("lost", 0.0), 0.0))
        rtt = max(1e-6, self._number(obs.get("rtt", 1.0), 1.0))
        timeout = bool(obs.get("timeout", False))
        self.last_t = t

        observed = acked + lost
        instant_loss = lost / observed if observed > 0.0 else 0.0

        decay = 0.90
        self.loss_mass = decay * self.loss_mass + lost
        self.packet_mass = decay * self.packet_mass + observed
        loss_ewma = (
            self.loss_mass / self.packet_mass
            if self.packet_mass > 1e-12
            else 0.0
        )

        if instant_loss >= 0.10:
            self.high_loss_ticks += 1
        else:
            self.high_loss_ticks = max(0, self.high_loss_ticks - 1)

        self._update_base_rtt(t, rtt, loss_ewma)
        base = max(1e-6, self.base_rtt)
        queue_delay = max(0.0, rtt - base)
        queue_ratio = queue_delay / base

        target_ratio = 0.15
        action = "hold"

        if timeout or (lost > 0.0 and acked <= 0.0):
            self.zero_ack_ticks += 1
        else:
            self.zero_ack_ticks = 0

        if timeout or self.zero_ack_ticks >= 1:
            self.cwnd = self._clamp(1.0)
            self.slow_start = False
            self.last_loss_reduce = t
            action = "timeout"
        else:
            severe_loss = (
                (instant_loss >= 0.18 and observed >= 8.0)
                or loss_ewma >= 0.12
                or self.high_loss_ticks >= 3
            )
            congestion_loss = (
                lost > 0.0
                and (
                    queue_ratio >= 0.08
                    or loss_ewma >= 0.035
                )
            )

            if severe_loss and t - self.last_loss_reduce >= max(1.0, rtt):
                self.cwnd *= 0.45
                self.slow_start = False
                self.last_loss_reduce = t
                action = "high_loss"
            elif congestion_loss and t - self.last_loss_reduce >= max(1.0, rtt):
                self.cwnd *= 0.5
                self.slow_start = False
                self.last_loss_reduce = t
                action = "congestion_loss"
            elif self.slow_start:
                if queue_ratio >= 0.10 or lost > 0.0:
                    self.slow_start = False
                    action = "exit_slow_start"
                else:
                    self.cwnd += acked
                    action = "slow_start"
            else:
                if queue_ratio <= target_ratio:
                    self.cwnd += acked / max(self.cwnd, 1.0)
                    action = "increase"
                elif t - self.last_delay_reduce >= max(1.0, rtt):
                    excess = (queue_ratio - target_ratio) / max(target_ratio, 1e-6)
                    factor = max(0.80, 0.98 - 0.04 * min(excess, 4.0))
                    self.cwnd *= factor
                    self.last_delay_reduce = t
                    action = "delay_reduce"
                else:
                    action = "delay_hold"

            self.cwnd = self._clamp(self.cwnd)

        telemetry = {
            "phase": "slow_start" if self.slow_start else "congestion_avoidance",
            "action": action,
            "base_rtt": float(base),
            "queue_delay": float(queue_delay),
            "queue_ratio": float(queue_ratio),
            "loss_rate": float(instant_loss),
            "loss_ewma": float(loss_ewma),
            "zero_ack_ticks": int(self.zero_ack_ticks),
        }
        return {"cwnd": float(self.cwnd), "telemetry": telemetry}
