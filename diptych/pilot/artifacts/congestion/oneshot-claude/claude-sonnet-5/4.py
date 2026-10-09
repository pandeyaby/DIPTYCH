"""Congestion control module.

Implements a Reno-style AIMD controller augmented with:
  * loss-rate classification, so isolated/low-rate (random) losses are
    not mistaken for congestion and do not trigger a window cut, while
    sustained/high loss rates do;
  * an RTT-based early-congestion-avoidance signal (a light Vegas/HyStart
    style check against an adaptive, windowed estimate of the minimum
    RTT) that caps growth once a standing queue starts to build, instead
    of growing all the way to a buffer-filling loss like plain Reno.
"""

from collections import deque


class Controller:
    def __init__(self, config: dict):
        self.min_cwnd = float(config.get("min_cwnd", 1.0))
        self.max_cwnd = float(config.get("max_cwnd", 2000.0))
        self.cwnd = float(config.get("initial_cwnd", 4.0))
        self.cwnd = min(max(self.cwnd, self.min_cwnd), self.max_cwnd)

        self.ssthresh = self.max_cwnd

        self.ewma_rtt = None
        self.rtt_window = deque()  # (t, rtt) pairs for windowed min-RTT
        self.min_rtt_window_ticks = 40

        self.loss_ewma = 0.0
        self.loss_alpha = 0.25
        self.loss_decay = 0.9
        self.low_loss_thresh = 0.04

        self.last_decrease_t = -1.0e18

        # Delay-based growth/holdback thresholds, expressed as ratios of
        # current smoothed RTT over the adaptive minimum RTT.
        self.ss_delay_cap = 1.3
        self.ca_hold_cap = 1.3
        self.ca_decay_cap = 2.5
        self.ca_decay_factor = 0.98

        self.t = 0.0

    def tick(self, obs: dict) -> dict:
        t = float(obs.get("t", self.t))
        self.t = t
        acked = float(obs.get("acked", 0.0))
        lost = float(obs.get("lost", 0.0))
        rtt = max(float(obs.get("rtt", 1.0)), 1e-3)
        timeout = bool(obs.get("timeout", False))

        if self.ewma_rtt is None:
            self.ewma_rtt = rtt
        else:
            self.ewma_rtt = 0.3 * rtt + 0.7 * self.ewma_rtt

        self.rtt_window.append((t, rtt))
        while self.rtt_window and self.rtt_window[0][0] < t - self.min_rtt_window_ticks:
            self.rtt_window.popleft()
        min_rtt = min(r for _, r in self.rtt_window)

        decreased = False
        congestion_state = False

        if timeout:
            self.ssthresh = max(self.min_cwnd, self.cwnd * 0.5)
            self.cwnd = self.min_cwnd
            self.last_decrease_t = t
            self.loss_ewma *= 0.5
            decreased = True
        else:
            sent = acked + lost
            if sent > 0:
                inst_loss = lost / sent
                self.loss_ewma = self.loss_alpha * inst_loss + (1 - self.loss_alpha) * self.loss_ewma
            else:
                self.loss_ewma *= self.loss_decay

            congestion_state = self.loss_ewma > self.low_loss_thresh

            if congestion_state and (t - self.last_decrease_t) >= self.ewma_rtt:
                self.ssthresh = max(self.min_cwnd, self.cwnd * 0.5)
                self.cwnd = self.ssthresh
                self.last_decrease_t = t
                decreased = True

            if not decreased and not congestion_state:
                queue_ratio = self.ewma_rtt / max(min_rtt, 1e-6)

                if self.cwnd < self.ssthresh:
                    if queue_ratio < self.ss_delay_cap:
                        self.cwnd += acked
                    else:
                        self.ssthresh = self.cwnd
                        self.cwnd += acked / self.cwnd
                else:
                    if queue_ratio < self.ca_hold_cap:
                        self.cwnd += acked / self.cwnd
                    elif queue_ratio < self.ca_decay_cap:
                        pass
                    else:
                        self.cwnd *= self.ca_decay_factor

        self.cwnd = min(max(self.cwnd, self.min_cwnd), self.max_cwnd)

        telemetry = {
            "t": t,
            "cwnd": self.cwnd,
            "ssthresh": self.ssthresh,
            "min_rtt": min_rtt,
            "ewma_rtt": self.ewma_rtt,
            "loss_ewma": self.loss_ewma,
            "congestion_state": int(congestion_state),
            "decreased": int(decreased),
            "timeout": int(timeout),
            "state": "slow_start" if self.cwnd < self.ssthresh else "congestion_avoidance",
        }

        return {"cwnd": self.cwnd, "telemetry": telemetry}
