"""Delay-led AIMD congestion controller.

The window is steered by queueing delay (``rtt`` minus the path's minimum
``rtt``) rather than by individual losses:

* slow start doubles the window per round trip until a queue appears;
* congestion avoidance adds ``velocity`` packets per round trip, where
  ``velocity`` is 1 and doubles per round trip only while the queue has stayed
  empty for several round trips (the link is clearly under-used);
* when the queueing delay exceeds a fraction of the base round-trip time the
  window is cut multiplicatively, at most once per round trip.  The cut is
  deep enough to drain the queue, so every flow re-observes the true base
  delay each cycle and flows sharing a link back off together (AIMD, fair).

Losses are treated as congestion only when there is evidence that they are:

* a loss while the delay sits at the top of its recently observed range (the
  buffer is full, some other flow is filling it) halves the window, as the
  standard algorithm would;
* a high loss rate sustained over consecutive round trips cuts the window
  every round trip regardless of delay;
* a timeout halves the window and a second consecutive one collapses it.

Isolated losses with an empty queue are taken to be random and ignored.

If delay-triggered cuts fail to bring the delay down, the delay is not a queue
this flow is causing (the path's minimum delay has changed, or another flow
owns the queue), so the base delay is re-measured and the cuts are undone; if
the rtt did not move at all, the window is scaled to keep the earlier rate.

The base delay can be wrong when the queue has never been seen empty.  When
that is plausible (losses keep arriving although no queue is measured, or the
base was re-measured recently) the window is halved for one round trip as a
probe: if the rtt falls below the assumed base the reduction is kept and the
delay rule takes over, otherwise the window is restored.
"""

from __future__ import annotations

import math


class _Extremum:
    """Sliding-window min (sign=1) or max (sign=-1) as a monotonic queue."""

    def __init__(self, sign: float):
        self.sign = sign
        self.items: list[list[float]] = []   # [t, value], values monotonic

    def push(self, t: float, value: float, window: float) -> None:
        s, items = self.sign, self.items
        while items and s * items[-1][1] >= s * value:
            items.pop()
        items.append([t, value])
        while items[0][0] < t - window:
            items.pop(0)

    def get(self) -> float:
        return self.items[0][1]

    def reset(self) -> None:
        self.items = []


class Controller:
    DELAY_TARGET = 0.2        # tolerated queueing delay, as a fraction of base rtt
    EMPTY_DELAY = 0.05        # queueing delay below this fraction counts as "no queue"
    DELAY_DECREASE = 0.7      # window factor on a delay signal / sustained loss
    LOSS_DECREASE = 0.5       # window factor on a congestive (buffer-full) loss
    HIGH_LOSS = 0.10          # per-round loss fraction regarded as high
    HIGH_LOSS_ROUNDS = 2      # consecutive high-loss rounds before reacting
    EMPTY_ROUNDS = 3          # empty-queue rounds before velocity starts doubling
    INEFFECTIVE_DROP = 0.75   # a cut "worked" if delay fell below this share of its old value
    INEFFECTIVE_CUTS = 2      # consecutive useless cuts before re-measuring the base rtt
    FLAT = 0.01               # rtt varying by less than this fraction is "not moving"
    FILTER_ROUNDS = 100.0     # min/max rtt filter length, in round trips
    PROBE_LOSS_ROUNDS = 16    # rounds with unexplained loss before a drain probe
    PROBE_AFTER_REBASE = 8    # rounds after re-measuring the base rtt before verifying it

    def __init__(self, config: dict):
        self.lo = float(config["min_cwnd"])
        self.hi = float(config["max_cwnd"])
        self.cwnd = min(self.hi, max(self.lo, float(config["initial_cwnd"])))
        self.slow_start = True
        self.velocity = 1.0

        self.rtt_min = _Extremum(1.0)
        self.rtt_max = _Extremum(-1.0)

        self.cooldown_until = -1
        self.timeouts = 0

        # per-round accounting
        self.round_end = -1
        self.round_sent = 0.0
        self.round_lost = 0.0
        self.round_max_delay = 0.0
        self.high_loss_rounds = 0
        self.empty_rounds = 0

        # effectiveness of delay-triggered cuts
        self.check_at = -1                # tick at which the last delay cut is judged
        self.delay_at_cut = 0.0
        self.useless_cuts = 0
        self.cwnd_before_cuts = self.cwnd
        self.min_since_cut = 0.0
        self.max_since_cut = 0.0
        self.draining = False             # a delay cut is still emptying the queue

        # drain probe: halve the window for one round to look for a hidden queue
        self.loss_rounds = 0              # rounds with loss that we chose to ignore
        self.verify_in = -1               # rounds until a re-measured base is verified
        self.probe_until = -1
        self.probe_restore = 0.0
        self.probe_base = 0.0
        self.probe_min = 0.0

    # -- helpers ---------------------------------------------------------

    def _cut(self, factor: float, t: int, rtt: float) -> None:
        self.cwnd *= factor
        self.slow_start = False
        self.velocity = 1.0
        self.empty_rounds = 0
        self.cooldown_until = t + math.ceil(rtt)

    def _forget(self) -> None:
        """Drop pending delay-cut and probe bookkeeping after a stronger reaction."""
        self.check_at = -1
        self.useless_cuts = 0
        self.draining = False
        self.probe_until = -1
        self.loss_rounds = 0
        self.verify_in = -1

    # -- main entry ------------------------------------------------------

    def tick(self, obs: dict) -> dict:
        t = int(obs["t"])
        acked, lost, rtt = float(obs["acked"]), float(obs["lost"]), float(obs["rtt"])
        if not (rtt > 0.0) or math.isinf(rtt):
            rtt = self.rtt_min.get() if self.rtt_min.items else 1.0
        acked, lost = max(0.0, acked), max(0.0, lost)
        period = math.ceil(rtt)

        window = self.FILTER_ROUNDS * rtt
        self.rtt_min.push(t, rtt, window)
        self.rtt_max.push(t, rtt, window)
        self.min_since_cut = min(self.min_since_cut, rtt)
        self.max_since_cut = max(self.max_since_cut, rtt)
        self.probe_min = min(self.probe_min, rtt)
        base = self.rtt_min.get()
        delay = rtt - base
        target = self.DELAY_TARGET * base

        # Round bookkeeping: loss rate and whether the queue stayed empty.
        self.round_sent += acked + lost
        self.round_lost += lost
        self.round_max_delay = max(self.round_max_delay, delay)
        high_loss = False
        if t >= self.round_end:
            frac = self.round_lost / self.round_sent if self.round_sent > 0 else 0.0
            self.high_loss_rounds = self.high_loss_rounds + 1 if frac > self.HIGH_LOSS else 0
            high_loss = self.high_loss_rounds >= self.HIGH_LOSS_ROUNDS
            if self.round_lost > 0:
                self.loss_rounds += 1
            if self.verify_in > 0:
                self.verify_in -= 1
            if self.round_max_delay <= self.EMPTY_DELAY * base and self.round_lost <= 0:
                self.empty_rounds += 1
                if self.empty_rounds >= self.EMPTY_ROUNDS and not self.slow_start:
                    self.velocity = min(self.velocity * 2.0, max(1.0, self.cwnd / 2.0))
            else:
                self.empty_rounds = 0
            self.round_end = t + period
            self.round_sent = self.round_lost = self.round_max_delay = 0.0

        if delay > self.EMPTY_DELAY * base or lost > 0:
            self.velocity = 1.0       # a queue (or loss) has appeared: stop accelerating

        if delay <= self.EMPTY_DELAY * base:
            self.draining = False

        event = ""
        if obs["timeout"]:
            # Nothing got through: back off hard, fully if it persists.
            self.timeouts += 1
            self._cut(0.5 if self.timeouts == 1 else 0.0, t, rtt)
            self._forget()
            event = "timeout"
        else:
            self.timeouts = 0

            # End of a drain probe: keep the reduction only if it uncovered a
            # queue (the rtt fell below what we took to be its minimum).
            if self.probe_until >= 0 and t >= self.probe_until:
                self.probe_until = -1
                if self.probe_min >= self.probe_base * (1.0 - self.FLAT):
                    self.cwnd = max(self.cwnd, self.probe_restore)
                    event = "probe_restore"

            # Judge the previous delay cut: did the delay respond to it?
            rebased = False
            if self.check_at >= 0 and t >= self.check_at:
                self.check_at = -1
                if delay > self.INEFFECTIVE_DROP * self.delay_at_cut:
                    self.useless_cuts += 1
                    flat = self.max_since_cut - self.min_since_cut <= self.FLAT * rtt
                    if flat or self.useless_cuts >= self.INEFFECTIVE_CUTS:
                        # The delay is not a queue of our making: re-measure
                        # the base rtt and give back what the cuts took.  If
                        # the rtt did not move at all the path itself got
                        # longer, so keep the sending rate we had before.
                        restore = self.cwnd_before_cuts
                        if flat:
                            restore *= self.min_since_cut / (base * (1.0 + self.DELAY_TARGET))
                        for f in (self.rtt_min, self.rtt_max):
                            f.reset()
                            f.push(t, self.min_since_cut, window)
                        self.cwnd = max(self.cwnd, restore)
                        self.useless_cuts = 0
                        self.draining = False
                        self.verify_in = self.PROBE_AFTER_REBASE
                        rebased = True
                        event = "rebase"
                else:
                    self.useless_cuts = 0

            top = self.rtt_max.get()
            buffer_full = (top > base * (1.0 + self.FLAT)
                           and rtt >= base + 0.8 * (top - base))

            if rebased:
                pass
            elif high_loss:
                self._cut(self.DELAY_DECREASE, t, rtt)
                self._forget()
                event = "high_loss"
            elif t < self.cooldown_until:
                pass
            elif lost > 0 and buffer_full and delay <= target:
                # Loss at the highest delay this path shows, yet no queue by
                # our own measure: the base estimate cannot be trusted (the
                # queue has never been seen empty), so obey the loss.
                self._cut(self.LOSS_DECREASE, t, rtt)
                self._forget()
                event = "loss"
            elif delay > target:
                if self.useless_cuts == 0:
                    self.cwnd_before_cuts = self.cwnd
                self._cut(self.DELAY_DECREASE, t, rtt)
                self.check_at = self.cooldown_until
                self.delay_at_cut = delay
                self.min_since_cut = self.max_since_cut = rtt
                self.draining = True
                event = "delay"
            elif self.draining:
                pass                  # let the queue empty before growing again
            elif not self.slow_start and (self.loss_rounds >= self.PROBE_LOSS_ROUNDS
                                          or self.verify_in == 0):
                # Losses keep coming although we see no queue, or the base
                # rtt was re-measured recently.  Either way it may hide a
                # standing queue: drain for one round and look.
                self.probe_restore, self.probe_base, self.probe_min = self.cwnd, base, rtt
                self._cut(0.5, t, rtt)
                self._forget()
                self.probe_until = self.cooldown_until
                event = "probe"
            elif self.slow_start:
                self.cwnd += acked
            else:
                self.cwnd += self.velocity * acked / self.cwnd

        self.cwnd = min(self.hi, max(self.lo, self.cwnd))
        return {"cwnd": self.cwnd,
                "telemetry": {"cwnd": self.cwnd, "base_rtt": base, "queue_delay": delay,
                              "velocity": self.velocity, "slow_start": int(self.slow_start),
                              "event": event}}
