import math


class Controller:
    def __init__(self, config: dict):
        self.min_cwnd = self._number(config.get("min_cwnd", 1.0), 1.0)
        self.max_cwnd = self._number(config.get("max_cwnd", 2000.0), 2000.0)
        if self.max_cwnd < self.min_cwnd:
            self.max_cwnd = self.min_cwnd

        initial = self._number(config.get("initial_cwnd", 4.0), 4.0)
        self.cwnd = self._clamp(initial)
        self.ssthresh = self.max_cwnd
        self.base_rtt = None
        self.last_rtt = None
        self.startup = True

        self.loss_ewma = 0.0
        self.epoch_acked = 0.0
        self.epoch_lost = 0.0
        self.epoch_age = 0.0

        self.congestion_delay = None
        self.last_action = "init"

    @staticmethod
    def _number(value, default):
        try:
            value = float(value)
        except (TypeError, ValueError):
            return float(default)
        return value if math.isfinite(value) else float(default)

    def _clamp(self, value):
        if not math.isfinite(value):
            value = self.min_cwnd
        return min(self.max_cwnd, max(self.min_cwnd, float(value)))

    def tick(self, obs: dict) -> dict:
        acked = max(0.0, self._number(obs.get("acked", 0.0), 0.0))
        lost = max(0.0, self._number(obs.get("lost", 0.0), 0.0))
        rtt = max(1e-6, self._number(obs.get("rtt", 1.0), 1.0))
        timeout = bool(obs.get("timeout", False))

        if self.base_rtt is None:
            self.base_rtt = rtt

        abrupt_delay_change = (
            self.last_rtt is not None
            and lost == 0.0
            and not timeout
            and rtt >= 1.6 * self.base_rtt
            and rtt >= 1.45 * self.last_rtt
        )

        if abrupt_delay_change:
            self.base_rtt = rtt
            self.startup = True
            self.ssthresh = self.max_cwnd
            self.last_action = "base-rtt-change"
        elif rtt < self.base_rtt:
            self.base_rtt = rtt

        qdelay = max(0.0, rtt - self.base_rtt)
        qfrac = qdelay / max(self.base_rtt, 1e-6)

        if self.congestion_delay is None:
            target_delay = 0.12 * self.base_rtt
        else:
            target_delay = min(
                0.12 * self.base_rtt,
                max(0.01 * self.base_rtt, 0.30 * self.congestion_delay),
            )
        target_frac = target_delay / max(self.base_rtt, 1e-6)

        self.epoch_acked += acked
        self.epoch_lost += lost
        self.epoch_age += 1.0

        if timeout:
            self.ssthresh = max(self.min_cwnd, self.cwnd * 0.5)
            self.cwnd = self.min_cwnd
            self.startup = False
            self.loss_ewma = max(self.loss_ewma, 0.5)
            self.epoch_acked = 0.0
            self.epoch_lost = 0.0
            self.epoch_age = 0.0
            self.last_action = "timeout"
            self.last_rtt = rtt
            return self._result(rtt, qdelay, target_delay)

        reduced = False
        if self.epoch_age >= max(1.0, rtt):
            total = self.epoch_acked + self.epoch_lost
            loss_rate = self.epoch_lost / total if total > 0.0 else 0.0
            self.loss_ewma = 0.75 * self.loss_ewma + 0.25 * loss_rate

            delay_congested = qfrac > max(0.08, 1.35 * target_frac)
            sustained_loss = self.loss_ewma >= 0.07
            congestion_loss = loss_rate > 0.0 and (
                delay_congested or sustained_loss
            )

            if congestion_loss:
                self.ssthresh = max(self.min_cwnd, self.cwnd * 0.5)
                self.cwnd = self.ssthresh
                self.startup = False
                reduced = True
                self.last_action = "loss-backoff"

                if qdelay > 0.0 and delay_congested:
                    if self.congestion_delay is None:
                        self.congestion_delay = qdelay
                    else:
                        self.congestion_delay = (
                            0.7 * self.congestion_delay + 0.3 * qdelay
                        )

            self.epoch_acked = 0.0
            self.epoch_lost = 0.0
            self.epoch_age = 0.0

        if not reduced and acked > 0.0:
            if self.startup:
                self.cwnd += min(acked, self.cwnd)
                self.last_action = "slow-start"
                if qfrac > 0.08 or self.cwnd >= self.ssthresh:
                    self.startup = False
            else:
                progress = min(1.0, acked / max(self.cwnd, 1e-6))
                if qdelay < target_delay:
                    headroom = 1.0 - qdelay / max(target_delay, 1e-6)
                    self.cwnd += progress * max(0.05, headroom)
                    self.last_action = "additive-increase"
                elif qdelay > target_delay:
                    excess = (qdelay - target_delay) / max(target_delay, 1e-6)
                    fraction = min(0.30, 0.30 * excess)
                    self.cwnd -= self.cwnd * fraction * progress
                    self.last_action = "delay-backoff"
                else:
                    self.last_action = "hold"

        self.cwnd = self._clamp(self.cwnd)
        self.last_rtt = rtt
        return self._result(rtt, qdelay, target_delay)

    def _result(self, rtt, qdelay, target_delay):
        self.cwnd = self._clamp(self.cwnd)
        return {
            "cwnd": float(self.cwnd),
            "telemetry": {
                "mode": self.last_action,
                "base_rtt": float(self.base_rtt),
                "rtt": float(rtt),
                "queue_delay": float(qdelay),
                "target_delay": float(target_delay),
                "loss_ewma": float(self.loss_ewma),
                "ssthresh": float(self.ssthresh),
            },
        }
