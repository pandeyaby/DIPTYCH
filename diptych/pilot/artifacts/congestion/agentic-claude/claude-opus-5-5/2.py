"""Delay-bounded AIMD congestion controller.

Reno-style additive increase (one packet per round trip, slow start below
``ssthresh``) with three congestion signals, each acted on at most once per
round trip:

* **Timeout** - nothing got through: collapse to the minimum window.
* **Congestive loss** - a loss seen while the queue is clearly built up, or a
  smoothed loss rate above ``LOSS_RATE_LIMIT``: halve, as Reno does.
* **Delay** - queueing delay above ``DELAY_TARGET`` of the base round-trip
  time: shrink the window just far enough to drain the queue. This is what
  keeps the queue short, and it makes the flow yield to loss-based flows.

Isolated losses seen while the queue is short are treated as random and
ignored, so a lossy path does not collapse the window.

The base round-trip time is the minimum seen. A flow that starts while a
queue is standing would overestimate it and then out-compete flows that know
better, so every ``PROBE_ROUNDS`` round trips the window is held at half for
one round trip to let the queue empty. If the base rises (a path change),
delay backoff would otherwise never stop; that case is recognised by the
round-trip time not responding at all to our own window reductions, and the
base estimate and the window are then restored.
"""

from __future__ import annotations

import math


class Controller:
    DELAY_TARGET = 0.10         # tolerated queueing delay, fraction of base RTT
    DELAY_DECREASE_MAX = 0.85   # gentlest multiplicative decrease on delay
    DRAIN = 0.9                 # delay backoff aims this far below the pipe size, so the
                                # queue empties and every flow sees the true base RTT
    EMPTY_GAIN = 0.2            # extra growth per round trip while the queue is empty
    LOSS_DECREASE = 0.5         # multiplicative decrease on congestive loss
    LOSS_DELAY_FACTOR = 2.0     # loss is congestive above this many delay targets
    LOSS_RATE_LIMIT = 0.03      # ... or when the smoothed loss rate exceeds this
    LOSS_RATE_TICKS = 24.0      # time constant of the loss-rate estimate
    LOSS_RATE_MIN_SENT = 50.0   # packets; keeps one early loss from looking like a high rate
    FLOOR_TICKS = 150           # half-length of the long-term minimum-RTT window
    FLAT_TOLERANCE = 1e-3       # relative RTT spread still counted as "did not respond"
    FLAT_LOSS_LIMIT = 0.02      # a full buffer also looks flat, but it drops packets
    FLAT_ROUNDS = 1             # unresponsive round trips needed to re-base ...
    FLAT_ROUNDS_PINNED = 6      # ... or this many when the window cannot be cut further
    PROBE_ROUNDS = 50           # round trips between base-RTT probes
    PROBE_DECREASE = 0.5        # window factor held for one round trip while probing
    TIMEOUT_CWND = 1.0

    def __init__(self, config: dict):
        self.lo = float(config["min_cwnd"])
        self.hi = float(config["max_cwnd"])
        self.cwnd = min(self.hi, max(self.lo, float(config["initial_cwnd"])))
        self.ssthresh = self.hi
        self.hold_until = -1            # no further decrease before this tick
        self.in_timeout = False

        self.probe_at = None            # tick of the next base-RTT probe
        self.probe_until = -1           # a probe is in progress before this tick
        self.probe_saved = 0.0          # window to return to after the probe

        self.base_rtt = math.inf        # minimum RTT, used for delay control
        self.floor_cur = math.inf       # long-window minimum RTT (two rotating
        self.floor_prev = math.inf      # buckets), used to classify losses
        self.floor_rotate_at = None

        self.sent_avg = 0.0             # decayed sums for the loss rate
        self.lost_avg = 0.0

        # Delay-backoff episode: consecutive decreases with delay still high.
        self.ep_active = False
        self.ep_prior_cwnd = 0.0        # window before the episode's first decrease
        self.ep_reduced = False         # the last decrease really cut the window
        self.ep_rtt_min = math.inf      # since the last decrease
        self.ep_rtt_max = 0.0
        self.ep_sent = 0.0
        self.ep_lost = 0.0
        self.ep_flat = 0

    # -- helpers ----------------------------------------------------------

    def _observe_rtt(self, t: int, rtt: float) -> None:
        self.base_rtt = min(self.base_rtt, rtt)
        if self.floor_rotate_at is None:
            self.floor_rotate_at = t + self.FLOOR_TICKS
        elif t >= self.floor_rotate_at:
            self.floor_prev, self.floor_cur = self.floor_cur, math.inf
            self.floor_rotate_at = t + self.FLOOR_TICKS
        self.floor_cur = min(self.floor_cur, rtt)

    def _end_episode(self) -> None:
        self.ep_active = False
        self.ep_flat = 0

    def _restart_round(self) -> None:
        self.ep_rtt_min, self.ep_rtt_max = math.inf, 0.0
        self.ep_sent = self.ep_lost = 0.0

    def _delay_backoff(self, t: int, rtt: float, round_ticks: int) -> None:
        if self.ep_active:
            spread = self.ep_rtt_max - self.ep_rtt_min
            flat = (spread <= self.FLAT_TOLERANCE * self.ep_rtt_min
                    and self.ep_lost <= self.FLAT_LOSS_LIMIT * self.ep_sent)
            # A round after a real cut is strong evidence; a round pinned at
            # the minimum window is weak evidence, so more of them are needed.
            weight = self.FLAT_ROUNDS_PINNED // self.FLAT_ROUNDS if self.ep_reduced else 1
            self.ep_flat = self.ep_flat + weight if flat else 0
            if self.ep_flat >= self.FLAT_ROUNDS_PINNED:
                # The delay is not ours to remove: the path got longer.
                self.base_rtt = self.ep_rtt_min
                self.cwnd = max(self.cwnd, self.ep_prior_cwnd)
                self.ssthresh = self.cwnd
                self.hold_until = t + round_ticks
                self.probe_at = t + self.PROBE_ROUNDS * round_ticks
                self._end_episode()
                return
        else:
            self.ep_active = True
            self.ep_prior_cwnd = self.cwnd
        before = self.cwnd
        factor = min(self.DELAY_DECREASE_MAX, max(self.LOSS_DECREASE, self.DRAIN * self.base_rtt / rtt))
        self.cwnd = max(self.lo, self.cwnd * factor)
        self.ssthresh = max(self.cwnd, 2.0)
        self.ep_reduced = self.cwnd < 0.95 * before
        self.hold_until = t + round_ticks
        self._restart_round()

    # -- main entry -------------------------------------------------------

    def tick(self, obs: dict) -> dict:
        t, acked, lost = int(obs["t"]), float(obs["acked"]), float(obs["lost"])
        rtt = float(obs["rtt"])
        if not (rtt > 0.0 and math.isfinite(rtt)):
            rtt = self.base_rtt if math.isfinite(self.base_rtt) else 1.0
        round_ticks = max(2, math.ceil(rtt))
        self._observe_rtt(t, rtt)

        decay = 1.0 - 1.0 / self.LOSS_RATE_TICKS
        self.sent_avg = self.sent_avg * decay + acked + lost
        self.lost_avg = self.lost_avg * decay + lost
        loss_rate = self.lost_avg / max(self.sent_avg, self.LOSS_RATE_MIN_SENT)

        self.ep_rtt_min = min(self.ep_rtt_min, rtt)
        self.ep_rtt_max = max(self.ep_rtt_max, rtt)
        self.ep_sent += acked + lost
        self.ep_lost += lost

        floor = min(self.floor_cur, self.floor_prev)
        over_target = rtt - self.base_rtt > self.DELAY_TARGET * self.base_rtt
        congestive = lost > 0 and (
            rtt - floor > self.LOSS_DELAY_FACTOR * self.DELAY_TARGET * floor
            or loss_rate > self.LOSS_RATE_LIMIT)
        state = "increase"

        if obs["timeout"]:
            # Halve the slow-start target once per outage, not once per tick of
            # it, and not again while still recovering from the previous one.
            if not self.in_timeout and (self.cwnd >= self.ssthresh or self.ssthresh >= self.hi):
                self.ssthresh = max(self.cwnd * self.LOSS_DECREASE, 2.0)
            self.in_timeout = True
            self.cwnd = self.TIMEOUT_CWND
            self.hold_until = t + round_ticks
            self.probe_until = -1
            self.probe_at = t + self.PROBE_ROUNDS * round_ticks
            self._end_episode()
            state = "timeout"
        elif t < self.probe_until:
            # Holding a reduced window so the queue we share can empty and
            # reveal the true base RTT (picked up by _observe_rtt).
            self.in_timeout = False
            state = "probe_rtt"
            if congestive and t >= self.hold_until:
                self.probe_saved = max(self.probe_saved * self.LOSS_DECREASE, 2.0)
                self.hold_until = t + round_ticks
            if t + 1 >= self.probe_until:
                self.cwnd = self.ssthresh = self.probe_saved
                self.probe_at = t + self.PROBE_ROUNDS * round_ticks
        elif (self.probe_at is not None and t >= self.probe_at
              and self.cwnd >= self.ssthresh and not congestive and not over_target):
            self.in_timeout = False
            state = "probe_rtt"
            self.probe_saved = self.cwnd
            self.cwnd = max(self.lo, self.cwnd * self.PROBE_DECREASE)
            self.probe_until = t + round_ticks
            self._end_episode()
        else:
            self.in_timeout = False
            if self.probe_at is None:
                self.probe_at = t + self.PROBE_ROUNDS * round_ticks
            if not over_target:
                self._end_episode()
            if congestive:
                state = "loss"
                if t >= self.hold_until:
                    self.ssthresh = max(self.cwnd * self.LOSS_DECREASE, 2.0)
                    self.cwnd = self.ssthresh
                    self.hold_until = t + round_ticks
                    self._end_episode()
            elif over_target:
                state = "delay"
                if t >= self.hold_until:
                    self._delay_backoff(t, rtt, round_ticks)
            elif self.cwnd < self.ssthresh:
                self.cwnd = min(self.cwnd + acked, max(self.ssthresh, self.cwnd))
                state = "slow_start"
            else:
                self.cwnd += acked / self.cwnd
                if rtt - self.base_rtt <= self.FLAT_TOLERANCE * self.base_rtt:
                    # No queue at all: the link is idle, so nobody is delayed
                    # by closing the gap faster than one packet per round.
                    self.cwnd += self.EMPTY_GAIN * acked
                    state = "probe"

        self.cwnd = min(self.hi, max(self.lo, self.cwnd))
        return {"cwnd": self.cwnd,
                "telemetry": {"cwnd": self.cwnd, "ssthresh": self.ssthresh,
                              "base_rtt": self.base_rtt, "loss_rate": loss_rate,
                              "state": state}}
