"""Delay-based congestion controller with a loss backstop.

The window is steered by the flow's own estimated backlog at the bottleneck,
``backlog = cwnd * (rtt - base_rtt) / rtt`` packets (as in Vegas / FAST):

* slow start doubles the window per round trip until the backlog reaches the
  target, the loss rate is high, or ``ssthresh`` is reached;
* below the target the window grows by at most one packet per round trip
  (never faster than the standard algorithm);
* above the target the excess backlog is removed within a round trip.

A single loss is not taken as congestion, so a low random loss rate does not
collapse the window. Loss still reduces the window in four ways:

* a timeout collapses the window to the minimum;
* a smoothed loss rate above ``LOSS_TOLERANCE`` shrinks the window every round
  trip for as long as it persists;
* a loss that arrives after rising delay has already pushed the window down
  (another flow is filling the buffer) halves the window, at most once per
  round trip, and halves the backlog target;
* a loss seen while the backlog is below target means the buffer may be
  shallower than the target, so the target is lowered. It recovers slowly.

``base_rtt`` is the lowest round-trip time seen recently. Three things keep it
honest:

* every ``PROBE_INTERVAL`` round trips the window is cut for a moment so the
  queue drains and every flow on the link can see the path's own delay;
* if cutting the window leaves the delay exactly where it was, the delay is
  the path's and not a queue: ``base_rtt`` is raised to it and the window is
  rescaled to keep the sending rate held before the change;
* samples older than two ``BASE_WINDOW`` periods are forgotten.
"""

from __future__ import annotations

import math


class Controller:
    TARGET = 3.0             # packets of own backlog to hold at the bottleneck
    TARGET_MIN = 0.5         # floor for the adaptive target
    TARGET_CUT = 0.7         # on loss below target: target = TARGET_CUT * backlog
    TARGET_RECOVERY = 0.01   # fraction of TARGET regained per round trip
    INCREASE = 1.0           # packets per round trip below target
    MAX_DECREASE = 0.5       # largest delay-driven cut per round trip
    LOSS_TOLERANCE = 0.03    # smoothed loss rate tolerated without backing off
    LOSS_GAIN = 2.0          # cut per round trip = LOSS_GAIN * (loss - tolerance)
    LOSS_WINDOW = 4.0        # round trips of memory in the loss rate
    TIMEOUT_CWND = 1.0
    DECREASE_GAIN = 2.0      # excess backlog removed per round trip, as a multiple
    SQUEEZE_DELAY = 1.25     # a loss is congestion if queueing delay grew by this factor
    SQUEEZE_CUT = 0.05       # ... and the window was pushed down by this fraction
    DECREASE = 0.5           # window factor on a congestion loss, once per round trip
    BASE_WINDOW = 50.0       # round trips after which an old base_rtt sample expires
    PROBE_INTERVAL = 25.0    # round trips between drain probes
    PROBE_RTTS = (1.0, 3.0)  # shortest and longest drain probe, in round trips
    PROBE_DEPTH = 0.75       # first probe window as a fraction of the no-backlog window
    PROBE_DEEPEN = 0.8       # further window factor per tick while the delay keeps falling
    PROBE_MIN_DELAY = 0.05   # probe only if queueing delay exceeds this fraction of base_rtt
    PROBE_RESPONSE = 0.1     # fraction of the delay that must go away for it to be a queue
    FLAT_RTTS = 2.0          # round trips of flat delay before base_rtt is raised
    FLAT_TOLERANCE = 0.01    # "flat" = rtt range within this fraction of rtt
    FLAT_MIN_CUT = 0.2       # window cut needed before base_rtt is raised

    def __init__(self, config: dict):
        self.lo = float(config["min_cwnd"])
        self.hi = float(config["max_cwnd"])
        self.cwnd = min(self.hi, max(self.lo, float(config["initial_cwnd"])))
        self.ssthresh = self.hi
        self.slow_start = True
        self.in_timeout = False
        self.recover_until = -1
        self.base_rtt = math.inf
        self.base_recent = math.inf     # lowest rtt in the current expiry window
        self.base_rotate = None
        self.target = self.TARGET
        self.sent_avg = 0.0
        self.lost_avg = 0.0
        # Window and rtt the last time the backlog was at or below target.
        self.anchor_cwnd = None
        self.anchor_rtt = 0.0
        # Drain probe: the window is cut below its no-backlog value for a while.
        self.next_probe = None
        self.probe_until = None
        self.probe_since = 0
        self.probe_last = 0.0
        self.probe_cwnd = 0.0
        self.probe_rtt = 0.0
        self.probe_lo = 0.0
        self.probe_hi = 0.0
        self.probe_clean = True
        self.loss_run = 0           # consecutive ticks with loss
        # Stretch of flat rtt while the window is being cut.
        self.flat_since = None
        self.flat_lo = 0.0
        self.flat_hi = 0.0
        self.flat_cwnd = 0.0
        self.flat_backlog = 0.0

    def tick(self, obs: dict) -> dict:
        t, acked, lost = int(obs["t"]), float(obs["acked"]), float(obs["lost"])
        rtt = max(float(obs["rtt"]), 1e-9)
        if self.base_rotate is None or t >= self.base_rotate:
            # base_rtt forgets anything older than two windows.
            self.base_rtt, self.base_recent = min(self.base_recent, rtt), rtt
            self.base_rotate = t + self.BASE_WINDOW * rtt
        self.base_recent = min(self.base_recent, rtt)
        self.base_rtt = min(self.base_rtt, rtt)

        memory = 1.0 - 1.0 / max(1.0, self.LOSS_WINDOW * rtt)
        self.sent_avg = self.sent_avg * memory + acked + lost
        self.lost_avg = self.lost_avg * memory + lost
        # One lost packet is an event, not a rate: discount it.
        loss_rate = max(0.0, self.lost_avg - 1.0) / self.sent_avg if self.sent_avg > 0 else 0.0
        self.loss_run = self.loss_run + 1 if lost > 0 else 0

        if obs["timeout"]:
            # ssthresh is set by the first timeout of a run and then held.
            if not self.in_timeout:
                self.ssthresh = max(self.cwnd / 2.0, 2.0)
            self.in_timeout = True
            self.cwnd = self.TIMEOUT_CWND
            self.slow_start = True
            self.flat_since = None
            self.probe_until = None
            self.anchor_cwnd, self.anchor_rtt = self.cwnd, rtt
            return self._result(loss_rate, 0.0)
        self.in_timeout = False

        if self.anchor_cwnd is None:
            self.anchor_cwnd, self.anchor_rtt = self.cwnd, rtt
        if self.next_probe is None:
            self.next_probe = t + self.PROBE_INTERVAL * rtt
        high_loss = loss_rate > self.LOSS_TOLERANCE
        if self.probe_until is not None:
            if not self._probe(t, rtt, high_loss):
                return self._result(loss_rate, 0.0)
        backlog = self.cwnd * (rtt - self.base_rtt) / rtt

        if lost > 0 and backlog < self.target:
            self.target = max(self.TARGET_MIN, min(self.target, self.TARGET_CUT * backlog))
            self.anchor_cwnd, self.anchor_rtt = self.cwnd, rtt
        else:
            self.target = min(self.TARGET, self.target + self.TARGET_RECOVERY * self.TARGET / rtt)

        if self.slow_start and (
            backlog >= self.target
            or self.cwnd >= self.ssthresh
            or high_loss
        ):
            self.slow_start = False
            self.ssthresh = self.cwnd

        if backlog <= self.target:
            self.anchor_cwnd, self.anchor_rtt = self.cwnd, rtt
            self.flat_since = None
        elif self._delay_is_path(t, rtt, lost, backlog):
            self.base_rtt = self.base_recent = self.flat_lo
            self.cwnd = max(self.cwnd, self.anchor_cwnd * self.base_rtt / self.anchor_rtt)
            self.flat_since = None
            backlog = self.cwnd * (rtt - self.base_rtt) / rtt

        if (
            t >= self.next_probe
            and not self.slow_start
            and not high_loss
            and lost <= 0
            and rtt - self.base_rtt > self.PROBE_MIN_DELAY * self.base_rtt
        ):
            self.probe_since, self.probe_until = t, t + self.PROBE_RTTS[1] * rtt
            self.probe_last = rtt
            self.probe_cwnd, self.probe_rtt = self.cwnd, rtt
            self.probe_lo, self.probe_hi, self.probe_clean = math.inf, 0.0, True
            self.cwnd = max(self.lo, self.PROBE_DEPTH * self.cwnd * self.base_rtt / rtt)
            self.flat_since = None
            return self._result(loss_rate, backlog)

        squeezed = (
            backlog > self.target
            and rtt - self.base_rtt >= self.SQUEEZE_DELAY * (self.anchor_rtt - self.base_rtt)
            and self.cwnd <= (1.0 - self.SQUEEZE_CUT) * self.anchor_cwnd
        )
        if lost > 0 and squeezed and t >= self.recover_until:
            # Rising delay has been pushing the window down and now the queue
            # overflows: back off like the standard algorithm.
            self.cwnd *= self.DECREASE
            self.target = max(self.TARGET_MIN, self.target * self.DECREASE)
            self.slow_start = False
            self.recover_until = t + math.ceil(rtt)
            backlog = self.cwnd * (rtt - self.base_rtt) / rtt
        if high_loss:
            cut = min(0.5, self.LOSS_GAIN * (loss_rate - self.LOSS_TOLERANCE))
            self.cwnd *= (1.0 - cut) ** (1.0 / rtt)
        if backlog > self.target:
            excess = self.DECREASE_GAIN * (backlog - self.target) / rtt
            limit = self.cwnd * (1.0 - (1.0 - self.MAX_DECREASE) ** (1.0 / rtt))
            self.cwnd -= min(excess, limit)
        elif high_loss:
            pass
        elif self.slow_start:
            self.cwnd = min(self.cwnd + acked, max(self.ssthresh, self.cwnd))
        else:
            step = min(self.INCREASE, self.target - backlog)
            self.cwnd += step * acked / self.cwnd

        return self._result(loss_rate, backlog)

    def _probe(self, t: int, rtt: float, high_loss: bool) -> bool:
        """Run the drain probe; True once it is over and normal control resumes."""
        self.probe_lo, self.probe_hi = min(self.probe_lo, rtt), max(self.probe_hi, rtt)
        self.probe_clean = self.probe_clean and self.loss_run < 2
        falling, self.probe_last = rtt < self.probe_last, rtt
        if t < self.probe_until and not high_loss:
            # Stop early once the queue has stopped draining.
            if falling or t - self.probe_since < self.PROBE_RTTS[0] * rtt:
                self.cwnd = max(self.lo, self.cwnd * self.PROBE_DEEPEN)
                return False
        delay = self.probe_rtt - self.base_rtt
        if (
            self.probe_clean
            and not high_loss
            and self.probe_lo > self.probe_rtt - self.PROBE_RESPONSE * delay
            and self.probe_hi - self.probe_lo <= self.FLAT_TOLERANCE * rtt
        ):
            # Draining our own backlog left the delay where it was: it is not a queue.
            self.base_rtt = self.base_recent = self.probe_lo
            kept = self.anchor_cwnd * self.base_rtt / self.anchor_rtt
            if kept > self.probe_cwnd:
                self.cwnd = max(self.cwnd, kept)
            else:
                # No memory of a higher rate: search for it again.
                self.slow_start, self.ssthresh = True, self.hi
        if not high_loss:
            self.cwnd = max(self.cwnd, self.probe_cwnd)
        self.probe_until = None
        self.next_probe = t + self.PROBE_INTERVAL * rtt
        self.anchor_cwnd, self.anchor_rtt = self.cwnd, rtt
        return True

    def _delay_is_path(self, t: int, rtt: float, lost: float, backlog: float) -> bool:
        """True when cutting the window has left the delay unchanged."""
        if self.loss_run >= 2:
            # Sustained loss: the delay may be a full buffer, not the path.
            self.flat_since = None
            return False
        if lost > 0:
            return False
        if self.flat_since is not None:
            lo, hi = min(self.flat_lo, rtt), max(self.flat_hi, rtt)
            if hi - lo <= self.FLAT_TOLERANCE * rtt:
                self.flat_lo, self.flat_hi = lo, hi
                return (
                    t - self.flat_since >= self.FLAT_RTTS * rtt
                    and self.cwnd <= max(self.lo, (1.0 - self.FLAT_MIN_CUT) * self.flat_cwnd)
                    and self.flat_cwnd - self.cwnd >= min(self.flat_backlog, self.flat_cwnd - self.lo)
                )
        self.flat_since = t
        self.flat_lo = self.flat_hi = rtt
        self.flat_cwnd = self.cwnd
        self.flat_backlog = backlog
        return False

    def _result(self, loss_rate: float, backlog: float) -> dict:
        self.cwnd = min(self.hi, max(self.lo, self.cwnd))
        return {"cwnd": self.cwnd, "telemetry": {
            "cwnd": self.cwnd, "base_rtt": self.base_rtt, "backlog": backlog,
            "target": self.target, "loss_rate": loss_rate,
            "slow_start": int(self.slow_start)}}
