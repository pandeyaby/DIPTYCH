"""Congestion control controller for a single flow on a shared bottleneck.

Hybrid delay + loss based AIMD controller:
  * exponential slow start, exiting on either real loss or a build-up of
    queueing delay (so it doesn't have to overshoot a large buffer before
    backing off),
  * congestion-avoidance phase that grows additively while both the
    observed queueing delay and the (smoothed) loss rate stay low,
  * multiplicative decreases gated to at most once per round trip, driven
    either by a persistently high loss rate (congestion-style losses) or by
    queueing delay crossing a threshold relative to the path's base RTT,
  * isolated / low-rate random loss is ignored as a backoff signal so the
    flow keeps utilizing a lossy path,
  * a windowed minimum-RTT filter that can still climb if the path's base
    RTT genuinely increases, instead of being stuck at a stale minimum
    forever,
  * an immediate full backoff (<= 2 packets) whenever a timeout is
    reported.
"""

from __future__ import annotations


class Controller:
    # Slow start exit threshold on normalized queueing delay.
    SS_EXIT_QNORM = 0.5

    # Loss-rate (EWMA) thresholds.
    LOSS_HI = 0.08
    LOSS_LO = 0.03

    # Normalized queueing delay thresholds (qdelay / min_rtt).
    Q_LO = 0.10
    Q_HI = 0.35

    # Multiplicative decrease factor for delay-triggered backoff.
    DELAY_DECREASE_FACTOR = 0.85

    # Growth scale used in the "dead zone" between Q_LO and Q_HI.
    MID_GROWTH_SCALE = 0.3

    EWMA_ALPHA = 0.15

    EPS = 1e-9

    def __init__(self, config: dict):
        self.min_cwnd = float(config.get("min_cwnd", 1.0))
        self.max_cwnd = float(config.get("max_cwnd", 2000.0))
        self.cwnd = float(config.get("initial_cwnd", 4.0))
        self.cwnd = min(max(self.cwnd, self.min_cwnd), self.max_cwnd)

        self.ss_done = False
        self.loss_ewma = 0.0

        self.min_rtt = None  # type: float | None
        self.window_min = None  # type: float | None
        self.window_start = 0

        self.last_decrease_tick = -1e18

    def tick(self, obs: dict) -> dict:
        t = obs.get("t", 0)
        acked = float(obs.get("acked", 0.0))
        lost = float(obs.get("lost", 0.0))
        rtt = float(obs.get("rtt", 1.0))
        timeout = bool(obs.get("timeout", False))

        rtt = max(rtt, self.EPS)

        if self.min_rtt is None:
            self.min_rtt = rtt
            self.window_min = rtt
            self.window_start = t

        # Windowed minimum-RTT filter: tracks the true minimum immediately,
        # but every window it re-bases to the min seen in that window so a
        # genuine increase in the path's base RTT is not stuck forever.
        self.window_min = min(self.window_min, rtt)
        window_len = max(50.0, 20.0 * self.min_rtt)
        if (t - self.window_start) >= window_len:
            self.min_rtt = self.window_min
            self.window_start = t
            self.window_min = rtt
        elif rtt < self.min_rtt:
            self.min_rtt = rtt

        total = acked + lost
        loss_rate = (lost / total) if total > 0 else 0.0
        if total > 0:
            self.loss_ewma = (1 - self.EWMA_ALPHA) * self.loss_ewma + self.EWMA_ALPHA * loss_rate

        qdelay = max(0.0, rtt - self.min_rtt)
        qnorm = qdelay / max(self.min_rtt, self.EPS)

        event = "none"

        if timeout:
            self.cwnd = min(self.cwnd, 2.0)
            self.cwnd = max(self.cwnd, self.min_cwnd)
            self.last_decrease_tick = t
            self.ss_done = False
            self.loss_ewma = 0.0
            event = "timeout"
        else:
            cooldown_elapsed = (t - self.last_decrease_tick) >= max(1.0, rtt)

            if not self.ss_done:
                if lost > 0:
                    self.cwnd = max(self.min_cwnd, self.cwnd * 0.5)
                    self.ss_done = True
                    self.last_decrease_tick = t
                    event = "ss_exit_loss"
                elif qnorm > self.SS_EXIT_QNORM:
                    self.cwnd = max(self.min_cwnd, self.cwnd * 0.9)
                    self.ss_done = True
                    self.last_decrease_tick = t
                    event = "ss_exit_delay"
                else:
                    self.cwnd = self.cwnd + acked
                    event = "ss_grow"
            else:
                if self.loss_ewma > self.LOSS_HI:
                    if cooldown_elapsed:
                        factor = max(0.3, 1.0 - 1.5 * self.loss_ewma)
                        self.cwnd = max(self.min_cwnd, self.cwnd * factor)
                        self.last_decrease_tick = t
                        event = "loss_decrease"
                    else:
                        event = "loss_hold"
                elif qnorm > self.Q_HI:
                    if cooldown_elapsed:
                        self.cwnd = max(self.min_cwnd, self.cwnd * self.DELAY_DECREASE_FACTOR)
                        self.last_decrease_tick = t
                        event = "delay_decrease"
                    else:
                        event = "delay_hold"
                elif qnorm < self.Q_LO and self.loss_ewma < self.LOSS_LO:
                    self.cwnd = self.cwnd + acked / max(self.cwnd, 1.0)
                    event = "grow"
                else:
                    self.cwnd = self.cwnd + self.MID_GROWTH_SCALE * acked / max(self.cwnd, 1.0)
                    event = "mid_grow"

        self.cwnd = min(max(self.cwnd, self.min_cwnd), self.max_cwnd)

        telemetry = {
            "t": int(t),
            "event": event,
            "mode": "slow_start" if not self.ss_done else "congestion_avoidance",
            "cwnd": float(self.cwnd),
            "rtt": float(rtt),
            "min_rtt": float(self.min_rtt),
            "qnorm": float(qnorm),
            "loss_ewma": float(self.loss_ewma),
        }

        return {"cwnd": float(self.cwnd), "telemetry": telemetry}
