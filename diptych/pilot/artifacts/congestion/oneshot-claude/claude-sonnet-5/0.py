import math


class Controller:
    def __init__(self, config: dict):
        self.min_cwnd = float(config.get("min_cwnd", 1.0))
        self.max_cwnd = float(config.get("max_cwnd", 2000.0))
        self.cwnd = self._clamp(float(config.get("initial_cwnd", 4.0)))

        self.slow_start = True
        self.min_rtt = float("inf")
        self.min_rtt_stamp = 0

        self.probing = False
        self.probe_end_tick = 0
        self.pre_probe_cwnd = self.cwnd

        self.decrease_cooldown = 0.0
        self.ewma_loss = 0.0
        self.consec_timeouts = 0

        self.PROBE_INTERVAL = 200
        self.SS_EXIT_CONGESTION = 0.15
        self.LOW_THRESH = 0.2
        self.HIGH_THRESH = 0.4
        self.BETA = 0.7
        self.SUSTAINED_LOSS_THRESH = 0.15

    def _clamp(self, cwnd: float) -> float:
        return max(self.min_cwnd, min(self.max_cwnd, cwnd))

    def tick(self, obs: dict) -> dict:
        t = int(obs.get("t", 0))
        acked = float(obs.get("acked", 0.0))
        lost = float(obs.get("lost", 0.0))
        rtt = float(obs.get("rtt", 1.0))
        rtt = rtt if rtt > 0 else 1e-6
        timeout = bool(obs.get("timeout", False))

        if timeout:
            self.consec_timeouts += 1
            self.cwnd = self._clamp(self.min_cwnd)
            self.slow_start = True
            self.decrease_cooldown = 0.0
            return {
                "cwnd": self.cwnd,
                "telemetry": {
                    "t": t, "event": "timeout", "slow_start": int(self.slow_start),
                    "consec_timeouts": self.consec_timeouts, "probing": int(self.probing),
                },
            }
        self.consec_timeouts = 0

        total = acked + lost
        loss_rate = (lost / total) if total > 0 else 0.0
        self.ewma_loss = 0.9 * self.ewma_loss + 0.1 * loss_rate

        if rtt < self.min_rtt:
            self.min_rtt = rtt
            self.min_rtt_stamp = t

        # ProbeRTT: periodically drain our own queue so a stale, self-inflated
        # min_rtt estimate (or a shifted path base RTT) can be re-measured.
        if not self.probing and (t - self.min_rtt_stamp) >= self.PROBE_INTERVAL:
            self.probing = True
            self.pre_probe_cwnd = self.cwnd
            self.cwnd = self._clamp(min(self.cwnd, 4.0))
            dur = max(2, min(10, int(math.ceil(rtt)) + 1))
            self.probe_end_tick = t + dur

        if self.probing:
            if t >= self.probe_end_tick:
                self.probing = False
                self.min_rtt_stamp = t
                self.decrease_cooldown = 0.0
                self.cwnd = self._clamp(self.pre_probe_cwnd)
            return {
                "cwnd": self.cwnd,
                "telemetry": {
                    "t": t, "event": "probe", "slow_start": int(self.slow_start),
                    "min_rtt": self.min_rtt, "probing": int(self.probing),
                },
            }

        congestion_level = max(0.0, (rtt - self.min_rtt) / self.min_rtt) if math.isfinite(self.min_rtt) and self.min_rtt > 0 else 0.0

        if self.decrease_cooldown > 0:
            self.decrease_cooldown = max(0.0, self.decrease_cooldown - 1.0)

        # A loss is treated as congestive (and acted on) only when corroborated
        # by either queueing delay or a sustained loss rate; isolated/random
        # losses with neither signal are left alone so throughput survives
        # on lossy-but-uncongested paths.
        congestive = lost > 0 and (
            congestion_level > self.HIGH_THRESH or self.ewma_loss > self.SUSTAINED_LOSS_THRESH
        )

        safe_cwnd = max(self.cwnd, 1e-6)

        if self.slow_start:
            if congestive or congestion_level > self.SS_EXIT_CONGESTION:
                self.slow_start = False
                if congestive and self.decrease_cooldown <= 0:
                    self.cwnd = max(self.min_cwnd, self.cwnd * self.BETA)
                    self.decrease_cooldown = max(1.0, rtt)
            else:
                self.cwnd = self.cwnd + acked
        else:
            if lost > 0:
                if congestive and self.decrease_cooldown <= 0:
                    self.cwnd = max(self.min_cwnd, self.cwnd * self.BETA)
                    self.decrease_cooldown = max(1.0, rtt)
            else:
                if congestion_level < self.LOW_THRESH:
                    self.cwnd = self.cwnd + (acked / safe_cwnd)
                elif congestion_level < self.HIGH_THRESH:
                    self.cwnd = self.cwnd + 0.5 * (acked / safe_cwnd)
                elif self.decrease_cooldown <= 0:
                    self.cwnd = max(self.min_cwnd, self.cwnd - (acked / safe_cwnd))

        self.cwnd = self._clamp(self.cwnd)

        return {
            "cwnd": self.cwnd,
            "telemetry": {
                "t": t,
                "slow_start": int(self.slow_start),
                "congestion_level": congestion_level,
                "min_rtt": self.min_rtt,
                "loss_rate": loss_rate,
                "ewma_loss": self.ewma_loss,
                "probing": int(self.probing),
            },
        }
