"""Congestion control: loss-based AIMD (Reno-style) with Veno-style loss
classification (random vs. congestive) and a gentle delay-based throttle
to limit self-induced queueing.
"""

import math


class Controller:
    def __init__(self, config: dict):
        self.min_cwnd = float(config.get("min_cwnd", 1.0))
        self.max_cwnd = float(config.get("max_cwnd", 2000.0))
        initial = float(config.get("initial_cwnd", 4.0))
        self.cwnd = self._clamp(initial)

        self.ssthresh = self.max_cwnd
        self.slow_start = True

        self.min_rtt = None
        self.rtt_ewma = None
        self.ticks_since_new_min = 0
        self.min_rtt_reset_interval = 100

        self.loss_ewma = 0.0
        self.last_backoff_tick = -10 ** 9
        self.t = -1

        # loss classification tuning (Veno-style)
        self.backlog_beta = 3.0
        self.congestive_factor = 0.5
        self.random_factor = 0.8

        # delay-based throttle tuning
        self.qratio_skip = 0.4
        self.qratio_decay = 0.7
        self.qratio_boost = 0.15
        self.decay_mult = 0.995
        self.boost_mult = 1.5

    def _clamp(self, v: float) -> float:
        return min(max(v, self.min_cwnd), self.max_cwnd)

    def _update_rtt(self, rtt: float) -> None:
        self.rtt_ewma = rtt if self.rtt_ewma is None else 0.75 * self.rtt_ewma + 0.25 * rtt

        if self.min_rtt is None or rtt < self.min_rtt:
            self.min_rtt = rtt
            self.ticks_since_new_min = 0
        else:
            self.ticks_since_new_min += 1
            # allow the base-RTT estimate to re-learn upward if the path's
            # minimum delay genuinely changes (no fresh minimum for a while)
            if self.ticks_since_new_min >= self.min_rtt_reset_interval:
                self.min_rtt = rtt
                self.ticks_since_new_min = 0

    def tick(self, obs: dict) -> dict:
        self.t = obs.get("t", self.t + 1)
        rtt = float(obs.get("rtt", self.rtt_ewma or 1.0))
        if rtt <= 0:
            rtt = self.rtt_ewma or 1.0
        acked = float(obs.get("acked", 0.0))
        lost = float(obs.get("lost", 0.0))
        timeout = bool(obs.get("timeout", False))

        self._update_rtt(rtt)

        total = acked + lost
        loss_frac = (lost / total) if total > 0 else 0.0
        self.loss_ewma = 0.9 * self.loss_ewma + 0.1 * loss_frac

        if timeout:
            self.cwnd = self.min_cwnd
            self.ssthresh = max(self.min_cwnd, self.cwnd)
            self.slow_start = True
            self.last_backoff_tick = self.t
            self.cwnd = self._clamp(self.cwnd)
            return {
                "cwnd": self.cwnd,
                "telemetry": {
                    "event": "timeout",
                    "ss": int(self.slow_start),
                    "loss_ewma": float(self.loss_ewma),
                },
            }

        min_rtt = self.min_rtt or rtt
        qdelay = max(0.0, rtt - min_rtt)
        qratio = (qdelay / min_rtt) if min_rtt > 0 else 0.0
        backlog = self.cwnd * (1.0 - (min_rtt / rtt)) if rtt > 0 else 0.0

        rtt_ticks = max(1.0, rtt)
        can_backoff = (self.t - self.last_backoff_tick) >= math.ceil(rtt_ticks)

        event = "none"

        if lost > 0:
            if can_backoff:
                if backlog >= self.backlog_beta:
                    self.cwnd = self._clamp(self.cwnd * self.congestive_factor)
                    event = "congestive_loss"
                else:
                    self.cwnd = self._clamp(self.cwnd * self.random_factor)
                    event = "random_loss"
                self.ssthresh = self.cwnd
                self.slow_start = False
                self.last_backoff_tick = self.t
            # no growth on a tick that experienced loss, whether or not we
            # actually backed off (at most one backoff per round trip)
        else:
            if self.slow_start:
                self.cwnd += acked
                if qratio > self.qratio_skip or self.cwnd >= self.ssthresh:
                    self.slow_start = False
                    self.ssthresh = self.cwnd
                event = "slow_start"
            else:
                if self.cwnd > 0:
                    inc = acked / self.cwnd
                    if qratio > self.qratio_decay:
                        self.cwnd *= self.decay_mult
                        inc = 0.0
                        event = "delay_decay"
                    elif qratio > self.qratio_skip:
                        inc = 0.0
                        event = "delay_hold"
                    elif qratio < self.qratio_boost:
                        inc *= self.boost_mult
                        event = "ca_boost"
                    else:
                        event = "ca"
                    self.cwnd += inc

        self.cwnd = self._clamp(self.cwnd)

        telemetry = {
            "event": event,
            "ss": int(self.slow_start),
            "ssthresh": float(self.ssthresh),
            "min_rtt": float(min_rtt),
            "rtt_ewma": float(self.rtt_ewma or 0.0),
            "qratio": float(qratio),
            "backlog": float(backlog),
            "loss_ewma": float(self.loss_ewma),
        }
        return {"cwnd": self.cwnd, "telemetry": telemetry}
