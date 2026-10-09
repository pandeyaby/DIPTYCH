"""Delay-led AIMD congestion controller with a loss-rate backstop.

The primary congestion signal is queueing delay: the window grows like the
standard algorithm (slow start, then one packet per round trip) until the
round-trip time exceeds the estimated base RTT by ``DELAY_FRAC``; it is then
cut to just under the estimated bandwidth-delay product and held until the
queue has drained. Every flow sees the same delay and cuts by the same factor,
so flows of this algorithm converge to equal shares, and a loss-based flow
sharing the link always sees this one yield first. If a cut fails to drain
the queue, another flow is filling it: the window then stops growing until
either a single cut drains the queue again or the queue has stayed empty for
``YIELD_RTTS`` round trips, so the yielded capacity is not taken back just to
be dumped at the next queue build-up.

Loss is the secondary signal, needed where the buffer is too shallow for
delay to show. Congestive loss comes and goes with the queue, whereas path
(non-congestive) loss is there on every tick, so the minimum loss fraction
over the last few round trips is taken as the path's background loss, capped
at ``LOSS_TOLERANCE``. Loss in excess of that background is treated exactly
as the standard algorithm treats loss: halve, at most once per round trip.
A halving is undone on the next tick if the queue then stands well below its
recent peak: a drop-tail queue is full right after it overflows, so that loss
was not congestion. Each undo resets the peak to the level it was granted at,
so loss that recurs at one queue level is not excused twice.
Sustained loss above the cap therefore keeps halving the window down to the
minimum. A timeout collapses the window to the minimum.

The base RTT is a running minimum that is refreshed two ways: a two-bucket
windowed minimum (slow fallback), and a fast path for a path change - if the
RTT sits perfectly flat above the estimate and a probing window cut does not
move it, the delay is path delay rather than queue, so the estimate is reset
and the window restored to carry the pre-cut delivery rate.
"""

from __future__ import annotations

import math


class Controller:
    DELAY_FRAC = 0.25        # queueing delay tolerated, as a fraction of base RTT
    DRAIN_GAIN = 0.85        # window after a delay cut, as a fraction of est. BDP
    DRAINED_FRAC = 0.02      # queue counts as drained below this fraction of base RTT
    LOSS_TOLERANCE = 0.04    # most background loss ever discounted as non-congestive
    LOSS_MARGIN = 0.002      # loss fraction above background that counts as congestion
    LOSS_WINDOW_RTTS = 2     # length of one background-loss minimum bucket, in RTTs
    LOSS_DECREASE = 0.5
    PEAK_FRAC = 0.9          # queueing delay, relative to its recent peak, taken as "full"
    TIMEOUT_CWND = 1.0
    YIELD_RTTS = 16          # round trips of empty queue before growing again after yielding
    FLAT_TOL = 1e-3          # relative RTT change below which the RTT counts as flat
    MIN_WINDOW_RTTS = 25     # length of one base-RTT minimum bucket, in base RTTs
    MIN_WINDOW_TICKS = 50

    def __init__(self, config: dict):
        self.lo = float(config["min_cwnd"])
        self.hi = float(config["max_cwnd"])
        self.cwnd = min(self.hi, max(self.lo, float(config["initial_cwnd"])))
        self.ssthresh = self.hi
        self.mode = "ss"                 # ss | ca | drain
        self.base = None                 # base RTT estimate
        self.bucket_min = None           # minimum RTT in the current bucket
        self.prev_bucket_min = None
        self.bucket_end = None
        self.peak = 0.0                  # recent maximum RTT, two buckets like the minimum
        self.bucket_peak = 0.0
        self.undo = None                 # (tick, cwnd, ssthresh, mode) to restore a halving
        self.last_rtt = None
        self.flat = 0                    # consecutive ticks of unchanged RTT
        self.probe_rtt = None            # RTT at which a flat-RTT probe cut was made
        self.prior_rate = None           # delivery rate before the current cut episode
        self.cut_rtt = 0.0
        self.next_cut = -1
        self.loss_recover = -1
        self.recent_cwnd = 0.0           # window before the latest delay cut
        self.recent_until = -1
        self.yielded = False             # a delay cut failed to drain the queue: someone else fills it
        self.clean_cut = False           # the current drain episode has needed only one cut
        self.drained_since = None
        self.sent_avg = 0.0
        self.lost_avg = 0.0
        self.loss_min = None             # minimum loss fraction in the current bucket
        self.prev_loss_min = None
        self.loss_bucket_end = -1

    # -- helpers ---------------------------------------------------------

    def _clamp(self) -> None:
        self.cwnd = min(self.hi, max(self.lo, self.cwnd))

    def _out(self) -> dict:
        self._clamp()
        sent = self.sent_avg
        return {"cwnd": self.cwnd,
                "telemetry": {"cwnd": self.cwnd, "ssthresh": self.ssthresh, "mode": self.mode,
                              "base_rtt": self.base if self.base is not None else 0.0,
                              "loss_rate": self.lost_avg / sent if sent > 0 else 0.0}}

    def _track_base(self, t: int, rtt: float) -> None:
        if self.base is None:
            self.base = self.bucket_min = self.peak = self.bucket_peak = rtt
            self.bucket_end = t + max(self.MIN_WINDOW_TICKS, math.ceil(self.MIN_WINDOW_RTTS * rtt))
            return
        self.base = min(self.base, rtt)
        self.bucket_min = min(self.bucket_min, rtt)
        self.peak = max(self.peak, rtt)
        self.bucket_peak = max(self.bucket_peak, rtt)
        if t >= self.bucket_end:
            self.peak = self.bucket_peak
            self.bucket_peak = rtt
            prev = self.bucket_min if self.prev_bucket_min is None else self.prev_bucket_min
            self.base = min(prev, self.bucket_min)
            self.prev_bucket_min = self.bucket_min
            self.bucket_min = rtt
            self.bucket_end = t + max(self.MIN_WINDOW_TICKS, math.ceil(self.MIN_WINDOW_RTTS * self.base))

    def _background_loss(self, t: int, frac: float, rtt: float) -> float:
        if self.loss_min is None or t >= self.loss_bucket_end:
            self.prev_loss_min = self.loss_min
            self.loss_min = frac
            self.loss_bucket_end = t + self.LOSS_WINDOW_RTTS * math.ceil(rtt)
        else:
            self.loss_min = min(self.loss_min, frac)
        prev = self.loss_min if self.prev_loss_min is None else self.prev_loss_min
        return min(prev, self.loss_min, self.LOSS_TOLERANCE)

    def _reset_base(self, t: int, rtt: float) -> None:
        self.base = self.bucket_min = rtt
        self.prev_bucket_min = None
        self.bucket_end = t + max(self.MIN_WINDOW_TICKS, math.ceil(self.MIN_WINDOW_RTTS * rtt))

    # -- main ------------------------------------------------------------

    def tick(self, obs: dict) -> dict:
        t, acked, lost = int(obs["t"]), float(obs["acked"]), float(obs["lost"])
        rtt = float(obs["rtt"])
        sent = acked + lost
        round_ticks = math.ceil(rtt) + 1

        if obs["timeout"]:
            self.ssthresh = max(self.cwnd * 0.5, 2.0)
            self.cwnd = self.TIMEOUT_CWND
            self.mode = "ss"
            self.flat = 0
            self.probe_rtt = self.prior_rate = self.undo = None
            self.last_rtt = None
            return self._out()

        rate_before = self.sent_avg - self.lost_avg
        g = min(0.25, 1.0 / (4.0 * max(rtt, 1.0)))
        self.sent_avg += g * (sent - self.sent_avg)
        self.lost_avg += g * (lost - self.lost_avg)
        lossy = sent > 0 and lost > (self._background_loss(t, lost / sent, rtt) + self.LOSS_MARGIN) * sent

        self._track_base(t, rtt)
        base = self.base
        if self.last_rtt is not None and abs(rtt - self.last_rtt) <= self.FLAT_TOL * rtt:
            self.flat += 1
        else:
            self.flat = 0
        self.last_rtt = rtt

        if rtt > base * (1.0 + self.DRAINED_FRAC):
            self.drained_since = None
        elif self.drained_since is None:
            self.drained_since = t
        if self.yielded and self.drained_since is not None and t - self.drained_since >= self.YIELD_RTTS * rtt:
            self.yielded = False

        undo, self.undo = self.undo, None
        if (undo is not None and undo[0] == t and not lossy
                and rtt - base < self.PEAK_FRAC * (self.peak - base)):
            self.cwnd, self.ssthresh, self.mode = max(self.cwnd, undo[1]), undo[2], undo[3]
            self.peak = self.bucket_peak = rtt
            self.loss_recover = t

        cut = False
        before = self.cwnd
        if (self.flat >= round_ticks and rtt > base * (1.0 + self.DRAINED_FRAC)
                and not lossy):
            # The RTT is steady but above the base estimate: either a standing
            # queue or a longer path. Cut and watch: a queue shrinks, a path
            # does not.
            if self.probe_rtt is not None and abs(rtt - self.probe_rtt) <= self.FLAT_TOL * rtt:
                self._reset_base(t, rtt)
                if self.prior_rate is not None:
                    # Same delivery rate as before, over the longer path.
                    self.cwnd = max(self.cwnd, self.prior_rate * rtt * self.DRAIN_GAIN)
                self.ssthresh = self.cwnd
                self.probe_rtt = self.prior_rate = None
                self.mode = "ca"
                self.flat = 0
                return self._out()
            if self.prior_rate is None:
                self.prior_rate = rate_before
            self.probe_rtt = rtt
            self.cwnd *= self.DRAIN_GAIN
            self.cut_rtt, self.next_cut = rtt, t + round_ticks
            self.mode = "drain"
            self.flat = 0
            cut = True
        elif self.mode == "drain":
            if rtt <= base * (1.0 + self.DRAINED_FRAC):
                self.mode = "ca"
                if self.clean_cut:
                    self.yielded = False     # one cut drained the queue: nobody else is filling it
                self.probe_rtt = self.prior_rate = None
            elif (rtt > base * (1.0 + self.DELAY_FRAC) and t >= self.next_cut
                    and rtt >= self.cut_rtt * (1.0 - self.FLAT_TOL)):
                # A round trip after the cut the queue is still not shrinking.
                self.cwnd *= base / rtt * self.DRAIN_GAIN
                self.cut_rtt, self.next_cut = rtt, t + round_ticks
                self.yielded, self.clean_cut = True, False
                cut = True
        elif rtt > base * (1.0 + self.DELAY_FRAC):
            self.prior_rate = rate_before
            self.probe_rtt = None
            self.clean_cut = True
            self.cwnd *= base / rtt * self.DRAIN_GAIN
            self.cut_rtt, self.next_cut = rtt, t + round_ticks
            self.mode = "drain"
            cut = True

        if cut:
            self.recent_cwnd, self.recent_until = before, t + round_ticks
        if lossy:
            if t >= self.loss_recover:
                # Never a smaller reduction than the standard algorithm's,
                # counting a delay cut made within the last round trip.
                ref = max(before, self.recent_cwnd) if t < self.recent_until else before
                if not cut and self.mode != "drain":
                    self.undo = (t + 1, before, self.ssthresh, self.mode)
                self.cwnd = min(self.cwnd, ref * self.LOSS_DECREASE)
                self.loss_recover = t + math.ceil(rtt)
                cut = True
                if self.mode == "ss":
                    self.mode = "ca"
        if cut:
            self.ssthresh = max(self.cwnd, 2.0)
        elif self.mode == "ss":
            self.cwnd += acked
            if self.cwnd >= self.ssthresh:
                self.mode = "ca"
        elif self.mode == "ca" and not self.yielded:
            self.cwnd += acked / self.cwnd
        return self._out()
