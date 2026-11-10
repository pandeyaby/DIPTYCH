class Controller:
    def __init__(self, config: dict):
        self.min_cwnd = float(config.get("min_cwnd", 1.0))
        self.max_cwnd = float(config.get("max_cwnd", 2000.0))
        self.cwnd = float(config.get("initial_cwnd", 4.0))
        self.cwnd = max(self.min_cwnd, min(self.max_cwnd, self.cwnd))

        self.ssthresh = self.max_cwnd
        self.in_slow_start = True

        self.base_rtt = None
        self.min_rtt_since_reset = None
        self.ticks_in_window = 0.0

        self.loss_rate = 0.0
        self.loss_alpha = 0.1
        self.high_loss_thresh = 0.12
        self.veno_thresh = 4.0
        self.vegas_alpha = 1.0
        self.vegas_beta = 3.0

        self.next_reaction_tick = -1.0

    def tick(self, obs: dict) -> dict:
        t = float(obs["t"])
        acked = float(obs["acked"])
        lost = float(obs["lost"])
        rtt = max(float(obs["rtt"]), 1e-6)
        timeout = bool(obs["timeout"])

        if self.base_rtt is None:
            self.base_rtt = rtt
            self.min_rtt_since_reset = rtt
        else:
            self.min_rtt_since_reset = min(self.min_rtt_since_reset, rtt)
            self.base_rtt = min(self.base_rtt, rtt)

        round_len = max(1.0, rtt)
        self.ticks_in_window += 1.0
        if self.ticks_in_window >= 8.0 * round_len:
            self.base_rtt = self.min_rtt_since_reset
            self.min_rtt_since_reset = rtt
            self.ticks_in_window = 0.0

        sample = acked + lost
        if sample > 0:
            inst = lost / sample
            self.loss_rate += self.loss_alpha * (inst - self.loss_rate)
        else:
            self.loss_rate *= 0.99
        self.loss_rate = max(0.0, min(1.0, self.loss_rate))

        queue_est = max(0.0, self.cwnd * (1.0 - self.base_rtt / rtt))

        state = "cong_avoid"
        did_cut = False

        if timeout:
            prev_cwnd = self.cwnd
            self.cwnd = max(self.min_cwnd, min(1.0, self.max_cwnd))
            self.ssthresh = max(self.min_cwnd, prev_cwnd / 2.0)
            self.in_slow_start = True
            self.next_reaction_tick = t + round_len
            state = "timeout"
            did_cut = True
        else:
            can_react = t >= self.next_reaction_tick

            if lost > 0:
                if self.in_slow_start:
                    self.in_slow_start = False
                congestive = (queue_est >= self.veno_thresh) or (
                    self.loss_rate >= self.high_loss_thresh
                )
                if congestive:
                    if can_react:
                        self.cwnd = max(self.min_cwnd, self.cwnd * 0.5)
                        self.ssthresh = self.cwnd
                        self.next_reaction_tick = t + round_len
                        did_cut = True
                        state = "loss_congestive"
                    else:
                        state = "loss_gated"
                else:
                    self.ssthresh = self.cwnd
                    state = "loss_random"

            if not did_cut:
                if self.in_slow_start:
                    self.cwnd += acked
                    if self.cwnd >= self.ssthresh:
                        self.in_slow_start = False
                    if lost == 0:
                        state = "slow_start"
                else:
                    incr = acked / self.cwnd if self.cwnd > 0 else 0.0
                    if queue_est < self.vegas_alpha:
                        self.cwnd += incr
                        if lost == 0:
                            state = "cong_avoid_grow"
                    elif queue_est > self.vegas_beta:
                        self.cwnd -= incr
                        if lost == 0:
                            state = "cong_avoid_shrink"
                    else:
                        if lost == 0:
                            state = "cong_avoid_hold"

        self.cwnd = max(self.min_cwnd, min(self.max_cwnd, self.cwnd))

        telemetry = {
            "state": state,
            "base_rtt": self.base_rtt,
            "queue_est": queue_est,
            "loss_rate": self.loss_rate,
            "ssthresh": self.ssthresh,
            "slow_start": 1 if self.in_slow_start else 0,
        }
        return {"cwnd": self.cwnd, "telemetry": telemetry}
