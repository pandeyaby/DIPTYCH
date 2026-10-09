"""Congestion control: AIMD on filtered congestion-loss signals plus a
Vegas/BBR-style windowed-min-RTT delay signal for proactive, low-latency
backoff. Random, non-congestive loss is tolerated; only loss that is either
sustained/high-rate or accompanied by queueing growth is treated as a
congestion signal.
"""

from collections import deque


def _clamp(x, lo, hi):
    return lo if x < lo else hi if x > hi else x


class Controller:
    # loss filtering
    LOSS_HIGH_RATE = 0.08          # windowed loss rate above this => congestion, regardless of queue
    LOSS_WITH_QUEUE_RATE = 0.0     # any loss (>0) counts as congestion once combined with queue signal
    QUEUE_SIGNAL_FOR_LOSS = 0.15   # relative RTT inflation that turns a loss into a congestion signal

    # delay-based proactive control (relative to min RTT)
    DELAY_LOW = 0.20               # above this: stop growing (plateau), don't cut
    DELAY_HIGH = 0.50              # above this: gentle multiplicative cut

    MD_FACTOR = 0.5                # multiplicative decrease on confirmed congestion loss
    GENTLE_FACTOR = 0.85           # gentle decrease on pure delay signal

    MIN_RTT_WINDOW_TICKS = 60      # re-baseline min RTT this often, to track real path changes
    LOSS_WINDOW_LEN = 30

    def __init__(self, config: dict):
        self.min_cwnd = float(config.get("min_cwnd", 1.0))
        self.max_cwnd = float(config.get("max_cwnd", 2000.0))
        self.cwnd = _clamp(float(config.get("initial_cwnd", 4.0)), self.min_cwnd, self.max_cwnd)

        self.ssthresh = self.max_cwnd
        self.slow_start = True

        self.srtt = None

        self.min_rtt = None
        self.min_rtt_candidate = None
        self.min_rtt_window_count = 0

        self.loss_window = deque(maxlen=self.LOSS_WINDOW_LEN)

        self.ticks_since_backoff = 1_000_000

    def tick(self, obs: dict) -> dict:
        acked = float(obs.get("acked", 0.0))
        lost = float(obs.get("lost", 0.0))
        rtt = float(obs.get("rtt", 1.0))
        timeout = bool(obs.get("timeout", False))
        if rtt <= 0:
            rtt = 1e-6

        # smoothed RTT
        self.srtt = rtt if self.srtt is None else 0.875 * self.srtt + 0.125 * rtt

        # windowed-min RTT baseline: ratchets down immediately, ratchets up only
        # after a full window of sustained higher RTT (so it doesn't confuse
        # genuine queueing with a real change in path base delay).
        if self.min_rtt is None:
            self.min_rtt = rtt
            self.min_rtt_candidate = rtt
        if rtt < self.min_rtt:
            self.min_rtt = rtt
        self.min_rtt_candidate = min(self.min_rtt_candidate, rtt)
        self.min_rtt_window_count += 1
        if self.min_rtt_window_count >= self.MIN_RTT_WINDOW_TICKS:
            self.min_rtt = self.min_rtt_candidate
            self.min_rtt_candidate = rtt
            self.min_rtt_window_count = 0

        self.loss_window.append((acked, lost))
        tot_acked = sum(a for a, _ in self.loss_window)
        tot_lost = sum(l for _, l in self.loss_window)
        tot = tot_acked + tot_lost
        loss_rate = (tot_lost / tot) if tot > 0 else 0.0

        self.ticks_since_backoff += 1

        queue_delay = max(0.0, rtt - self.min_rtt)
        rel_queue = (queue_delay / self.min_rtt) if self.min_rtt > 1e-9 else 0.0

        action = "grow"

        if timeout:
            # Full backoff: congestion persists, nothing gets through.
            self.cwnd = self.min_cwnd
            self.ssthresh = max(self.min_cwnd, self.cwnd)
            self.slow_start = True
            self.ticks_since_backoff = 0
            action = "timeout_collapse"
        else:
            rtt_ticks = max(1, round(self.srtt)) if self.srtt else 1
            is_congestion_loss = lost > 0 and (
                loss_rate > self.LOSS_HIGH_RATE or rel_queue > self.QUEUE_SIGNAL_FOR_LOSS
            )

            if is_congestion_loss:
                action = "loss_backoff_suppressed"
                if self.ticks_since_backoff >= rtt_ticks:
                    self.cwnd = max(self.min_cwnd, self.cwnd * self.MD_FACTOR)
                    self.ssthresh = self.cwnd
                    self.slow_start = False
                    self.ticks_since_backoff = 0
                    action = "loss_backoff"
            elif rel_queue > self.DELAY_HIGH:
                action = "delay_backoff_suppressed"
                if self.ticks_since_backoff >= rtt_ticks:
                    self.cwnd = max(self.min_cwnd, self.cwnd * self.GENTLE_FACTOR)
                    self.ssthresh = self.cwnd
                    self.slow_start = False
                    self.ticks_since_backoff = 0
                    action = "delay_backoff"
            elif rel_queue > self.DELAY_LOW:
                self.slow_start = False
                action = "plateau"
            else:
                if self.slow_start and self.cwnd < self.ssthresh:
                    self.cwnd += acked
                    action = "slow_start"
                else:
                    self.slow_start = False
                    if self.cwnd > 0:
                        self.cwnd += acked / self.cwnd
                    action = "congestion_avoidance"

        self.cwnd = _clamp(self.cwnd, self.min_cwnd, self.max_cwnd)

        telemetry = {
            "cwnd": self.cwnd,
            "ssthresh": self.ssthresh,
            "min_rtt": self.min_rtt,
            "srtt": self.srtt,
            "rel_queue": rel_queue,
            "loss_rate": loss_rate,
            "slow_start": int(self.slow_start),
            "action": action,
        }

        return {"cwnd": self.cwnd, "telemetry": telemetry}
