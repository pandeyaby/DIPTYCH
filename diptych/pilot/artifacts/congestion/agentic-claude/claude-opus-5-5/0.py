"""Delay-led AIMD congestion controller with a Reno-compatible fallback.

Two modes:

``delay``  The default. The window grows like the standard algorithm (slow
           start, then one packet per round trip) but backs off as soon as
           queueing delay exceeds a fraction of the base round-trip time, so
           the queue stays short. Isolated losses seen while the queue is
           short are treated as non-congestive and ignored.
``reno``   Entered when backing off repeatedly fails to shorten the queue,
           i.e. a loss-based flow is filling the buffer. The window then
           follows standard AIMD (never more aggressive than it), and the
           controller periodically holds its window to test whether the
           competitor has gone, returning to ``delay`` when the queue stops
           growing on its own.

Independent of mode: a sustained high loss rate always reduces the window,
and consecutive timeouts collapse it to the minimum.

The base RTT is the smallest RTT seen. It is raised only when the RTT stays
exactly constant, loss-free, above the estimate (the path got longer; the
previous sending rate is then restored). A flow that has not seen an empty
queue for a long time briefly drops to the minimum window to expose the true
base RTT, which keeps late-starting flows from overestimating it.
"""

from __future__ import annotations

import math


class Controller:
    DELAY_THRESH = 0.3      # back off when queueing delay exceeds this fraction of base RTT
    DRAIN = 0.85            # a delay decrease aims this far below the queue-free window
    IDLE_RTTS = 4.0         # round trips with an empty queue before probing up quickly
    IDLE_GAIN = 0.25        # window growth per packet acked while probing up
    PROBE_RTTS = 50.0       # round trips without seeing an empty queue before draining
    LOSS_DECREASE = 0.5     # multiplicative decrease on a congestive loss
    HIGH_LOSS = 0.04        # smoothed loss rate that always forces a decrease
    HIGH_LOSS_DECREASE = 0.7
    LOSS_WINDOW = 100.0     # packets over which the loss rate is smoothed
    FLAT_TICKS = 3          # identical RTT samples that prove the queue is not moving
    INEFFECTIVE_CUTS = 3    # delay cuts that failed to shorten the queue before going reno
    HOLD_RTTS = 2.0         # length of the competitor test after a reno decrease
    EPS = 1e-3              # relative RTT change regarded as real
    HIST = 12               # ticks of delivery-rate history

    def __init__(self, config: dict):
        self.lo = float(config["min_cwnd"])
        self.hi = float(config["max_cwnd"])
        self.cwnd = float(config["initial_cwnd"])
        self.ssthresh = self.hi
        self.slow_start = True
        self.mode = "delay"
        self.base_rtt = None            # estimate of the path's RTT without queueing
        self.max_rtt = 0.0              # slowly decaying peak RTT (buffer-full level)
        self.prev_rtt = None
        self.flat = 0                   # consecutive loss-free ticks with unchanged RTT
        self.recover_until = -1
        self.timeouts = 0               # consecutive timeouts
        self.sent_sum = 0.0             # decayed packet counters for the loss rate
        self.lost_sum = 0.0
        self.rates = []                 # recent per-tick delivery rates
        self.last_cut_rtt = None
        self.bad_cuts = 0
        self.hold_until = -1
        self.hold_min_rtt = None
        self.last_empty = None          # last tick the queue was seen empty
        self.probe_saved = None         # window to restore after a drain probe
        self.probe_prev_rtt = 0.0
        self.probe_end = -1

    # -- helpers ---------------------------------------------------------

    def _cut(self, factor: float, t: int, rtt: float) -> None:
        self.cwnd = max(self.lo, self.cwnd * factor)
        self.ssthresh = max(self.cwnd, 2.0)
        self.slow_start = False
        self.recover_until = t + math.ceil(rtt)

    def _grow(self, acked: float) -> None:
        if self.slow_start and self.cwnd < self.ssthresh:
            self.cwnd += acked
        else:
            self.slow_start = False
            self.cwnd += acked / self.cwnd

    def _enter(self, mode: str) -> None:
        self.mode = mode
        self.bad_cuts = 0
        self.last_cut_rtt = None
        self.hold_until = -1
        self.hold_min_rtt = None

    def _track_base(self, rtt: float, lost: float, sent: float) -> None:
        if self.base_rtt is None or rtt < self.base_rtt:
            self.base_rtt = rtt
        same = self.prev_rtt is not None and abs(rtt - self.prev_rtt) <= 1e-9 * rtt
        self.flat = self.flat + 1 if (same and lost <= 0 and sent > 0) else 0
        self.prev_rtt = rtt
        holding = self.hold_min_rtt is not None
        if self.flat >= self.FLAT_TICKS - 1 and rtt > self.base_rtt and not holding:
            # The RTT sits above the estimate without moving and without loss
            # while our window was changing: no queue is building or draining,
            # so the path itself got longer. Adopt the new base and restore
            # the sending rate we had before the change.
            jump = rtt / self.base_rtt
            self.base_rtt = rtt
            self.max_rtt = rtt
            if jump > 1.0 + self.DELAY_THRESH / 2 and len(self.rates) >= self.HIST:
                old = self.rates[: self.HIST // 2]
                self.cwnd = max(self.cwnd, sum(old) / len(old) * rtt)
                self.ssthresh = max(self.cwnd, 2.0)
                self.slow_start = False
            self.recover_until = -1
            self.bad_cuts = 0
            self.last_cut_rtt = None
        self.max_rtt = max(rtt, self.max_rtt - 0.01 * (self.max_rtt - self.base_rtt))

    # -- main entry ------------------------------------------------------

    def tick(self, obs: dict) -> dict:
        t, acked, lost = int(obs["t"]), float(obs["acked"]), float(obs["lost"])
        rtt = max(float(obs["rtt"]), 1e-9)
        sent = acked + lost

        if obs["timeout"]:
            loss_rate = self.lost_sum / max(self.sent_sum, self.LOSS_WINDOW / 2)
            # One empty tick can be a single unlucky loss on a thin flow;
            # a second in a row means nothing is getting through.
            self.timeouts += 1
            self.prev_rtt, self.flat = rtt, 0
            if self.probe_saved is not None:
                if self.timeouts == 1:
                    return self._out(loss_rate)
                self.ssthresh = max(self.probe_saved * self.LOSS_DECREASE, 2.0)
                self.probe_saved = None
            if self.timeouts == 1:
                self.ssthresh = max(self.cwnd * self.LOSS_DECREASE, 2.0)
                self.cwnd = max(self.lo, self.cwnd * self.LOSS_DECREASE)
            else:
                self.cwnd = self.lo
            self.slow_start = True
            self.recover_until = t + math.ceil(rtt)
            return self._out(loss_rate)
        self.timeouts = 0

        # Timeouts are handled above and kept out of the loss-rate estimate,
        # so an outage does not keep the window down after the path returns.
        decay = max(0.0, 1.0 - sent / self.LOSS_WINDOW)
        self.sent_sum = self.sent_sum * decay + sent
        self.lost_sum = self.lost_sum * decay + lost
        loss_rate = self.lost_sum / max(self.sent_sum, self.LOSS_WINDOW / 2)

        draining = self.prev_rtt is not None and rtt < self.prev_rtt * (1.0 - self.EPS)
        self._track_base(rtt, lost, sent)
        base = self.base_rtt
        q = rtt / base - 1.0                            # queueing delay relative to base
        can_cut = t >= self.recover_until
        # A loss is taken as congestive when the queue is at its recent peak
        # (the buffer is full) and this tick lost more than the path's
        # background loss rate explains.
        at_peak = (q > 0.02 and rtt >= self.max_rtt - 0.02 * base
                   and lost > 2.0 * loss_rate * sent)
        empty = self.flat >= self.FLAT_TICKS - 1 and rtt <= base * (1.0 + 1e-9)
        if empty or self.last_empty is None:
            self.last_empty = t

        if self.probe_saved is not None:
            # Drain probe: send at the minimum until the queue stops falling,
            # so the true base RTT becomes visible, then resume.
            if rtt >= self.probe_prev_rtt * (1.0 - 1e-9) or t >= self.probe_end:
                self.cwnd, self.probe_saved = self.probe_saved, None
                self.last_empty = t
                self.recover_until = t + math.ceil(rtt)
            self.probe_prev_rtt = rtt
            return self._out(loss_rate)

        if loss_rate > self.HIGH_LOSS:
            if can_cut:
                self._cut(self.HIGH_LOSS_DECREASE, t, rtt)
        elif self.mode == "delay":
            if lost > 0 and at_peak:
                if can_cut:
                    self._cut(self.LOSS_DECREASE, t, rtt)
            elif q > self.DELAY_THRESH:
                # While an earlier decrease is still visibly emptying the
                # queue, let it finish rather than cutting twice for one event.
                if can_cut and not draining:
                    if self.last_cut_rtt is not None and rtt >= self.last_cut_rtt - self.EPS * base:
                        self.bad_cuts += 1
                    else:
                        self.bad_cuts = 0
                    self.last_cut_rtt = rtt
                    self._cut(max(0.5, self.DRAIN * base / rtt), t, rtt)
                    if self.bad_cuts >= self.INEFFECTIVE_CUTS:
                        self._enter("reno")
            elif can_cut:
                self.bad_cuts = 0
                self.last_cut_rtt = None
                if not self.slow_start and t - self.last_empty > self.PROBE_RTTS * rtt:
                    self.probe_saved = self.cwnd
                    self.probe_prev_rtt = rtt
                    self.probe_end = t + math.ceil(2 * rtt)
                    self.cwnd = self.lo
                elif empty and self.flat >= self.IDLE_RTTS * rtt:
                    # The queue has stayed empty for several round trips: the
                    # link has spare capacity, so close the gap multiplicatively
                    # until a queue forms.
                    self.cwnd += self.IDLE_GAIN * acked
                else:
                    self._grow(acked)
        else:  # reno
            if self.hold_min_rtt is not None and can_cut:
                self.hold_min_rtt = min(self.hold_min_rtt, rtt)
            if lost > 0:
                if can_cut:
                    self._cut(self.LOSS_DECREASE, t, rtt)
                    self.hold_until = t + math.ceil(rtt) + math.ceil(self.HOLD_RTTS * rtt)
                    self.hold_min_rtt = math.inf
            elif self.hold_min_rtt is not None:
                # Window held after a decrease: once our own cut has taken
                # effect, a queue that grows anyway means someone else is
                # still pushing; otherwise we are alone.
                if rtt > self.hold_min_rtt + self.EPS * base:
                    self.hold_min_rtt = None
                    self._grow(acked)
                elif t >= self.hold_until:
                    self._enter("delay")
            else:
                self._grow(acked)

        self.rates.append(acked)
        if len(self.rates) > self.HIST:
            self.rates.pop(0)
        return self._out(loss_rate)

    def _out(self, loss_rate: float) -> dict:
        self.cwnd = min(self.hi, max(self.lo, self.cwnd))
        return {"cwnd": self.cwnd,
                "telemetry": {"cwnd": self.cwnd, "ssthresh": self.ssthresh, "mode": self.mode,
                              "base_rtt": self.base_rtt if self.base_rtt is not None else 0.0,
                              "loss_rate": loss_rate}}
