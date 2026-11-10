import collections


class Controller:
    def __init__(self, config: dict):
        self.min_cwnd = float(config.get("min_cwnd", 1.0))
        self.max_cwnd = float(config.get("max_cwnd", 2000.0))
        self.cwnd = self._clamp(float(config.get("initial_cwnd", 4.0)))

        self.mode = "slow_start"
        self.ssthresh = self.max_cwnd
        self.rtt_history = collections.deque(maxlen=200)
        self.ewma_loss = 0.0
        self.consec_low = 0.0
        self.last_decrease_tick = float("-inf")

        self.slow_start_queue_exit = 2.0
        self.ca_low_queue = 1.0
        self.ca_high_queue = 3.0
        self.loss_exit_slow_start = 0.05
        self.loss_backstop_thresh = 0.12
        self.low_rounds_to_restart_ss = 3.0

    def _clamp(self, cwnd: float) -> float:
        return max(self.min_cwnd, min(self.max_cwnd, cwnd))

    def tick(self, obs: dict) -> dict:
        t = obs["t"]
        acked = obs["acked"]
        lost = obs["lost"]
        rtt = max(float(obs["rtt"]), 1e-6)
        timeout = obs["timeout"]

        self.rtt_history.append(rtt)
        base_rtt = min(self.rtt_history)

        total = acked + lost
        loss_rate = (lost / total) if total > 0 else 0.0
        self.ewma_loss = 0.9 * self.ewma_loss + 0.1 * loss_rate

        if timeout:
            self.cwnd = self.min_cwnd
            self.mode = "slow_start"
            self.ssthresh = self.max_cwnd
            self.ewma_loss *= 0.5
            self.consec_low = 0.0
            self.last_decrease_tick = t
        else:
            queue_est = self.cwnd * max(0.0, rtt - base_rtt) / rtt

            backed_off = False
            if (self.ewma_loss > self.loss_backstop_thresh
                    and (t - self.last_decrease_tick) >= rtt):
                self.cwnd = self._clamp(
                    self.cwnd * (1.0 - min(0.5, self.ewma_loss))
                )
                self.ssthresh = self.cwnd
                self.mode = "ca"
                self.last_decrease_tick = t
                self.consec_low = 0.0
                backed_off = True

            if not backed_off:
                if self.mode == "slow_start":
                    if (queue_est > self.slow_start_queue_exit
                            or self.ewma_loss > self.loss_exit_slow_start):
                        self.mode = "ca"
                        self.ssthresh = self.cwnd
                        self.consec_low = 0.0
                    elif acked > 0:
                        self.cwnd = self._clamp(self.cwnd + self.cwnd / rtt)
                else:
                    if queue_est > self.ca_high_queue:
                        self.cwnd = self._clamp(self.cwnd - 1.0 / rtt)
                        self.consec_low = 0.0
                    elif queue_est < self.ca_low_queue:
                        self.cwnd = self._clamp(self.cwnd + 1.0 / rtt)
                        self.consec_low += 1.0
                        if self.consec_low >= self.low_rounds_to_restart_ss * rtt:
                            self.mode = "slow_start"
                            self.consec_low = 0.0
                    else:
                        self.consec_low = 0.0

        self.cwnd = self._clamp(self.cwnd)

        telemetry = {
            "mode": self.mode,
            "base_rtt": base_rtt,
            "ewma_loss": self.ewma_loss,
            "ssthresh": self.ssthresh,
        }

        return {"cwnd": self.cwnd, "telemetry": telemetry}
