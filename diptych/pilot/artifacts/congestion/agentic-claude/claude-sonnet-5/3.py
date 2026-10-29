"""Delay- and loss-aware congestion control.

Primary signal is queueing delay (Vegas-style): estimate how many of our own
packets are sitting in the bottleneck queue from the gap between the current
RTT and a baseline "empty queue" RTT, and grow while that gap is small,
shrink proportionally while it is large. This keeps the queue short (so it is
gentle on latency and on flows sharing the link) and, since it does not react
to loss directly, it rides out small amounts of non-congestive loss instead
of halving on every one of them.

The baseline RTT cannot just be the smallest RTT ever seen with the window
held open forever: a controller that keeps some steady-state queue (by
design, to get full throughput) would then see its own queueing contaminate
its own baseline and drift, mistaking a self-inflicted queue for "no
queueing" before converging to a much too high window. Instead the baseline
is refreshed periodically by a short active probe (as in BBR's ProbeRTT):
cut the window to a small value for a few ticks, let the queue drain, record
the RTT seen then, and resume from where growth left off. This also lets the
baseline track a genuine change in the path's minimum delay.

A second signal, an EWMA of the per-tick loss rate, adds a decrease
proportional to the window whenever loss is actually elevated. At low
(near-baseline, ~1%) loss this term is negligible next to the delay-based
additive increase, but once loss is sustained and high (tens of percent) it
dominates and drives the window down well below its pre-loss level, since
that kind of loss is usually not visible as extra queueing delay (it can
happen upstream of the queue).

A timeout (nothing acknowledged despite sending) is treated as a hard signal
of a dead/blocked path: the window is collapsed immediately, independent of
both signals above, and slow start is re-armed so the flow can recover once
the path returns.
"""

from __future__ import annotations

from collections import deque


class Controller:
    ALPHA = 0.5           # packets of estimated queueing below which we grow
    BETA = 1.5            # packets of estimated queueing above which we shrink
    TARGET = (ALPHA + BETA) / 2.0
    SS_EXIT_DIFF = ALPHA     # queueing (packets) that ends slow start
    SS_EXIT_LOSS_STREAK = 2  # consecutive lossy ticks that end slow start
    LOSS_GAIN = 1.0          # strength of the loss-proportional decrease term
    LOSS_EWMA_K = 0.15
    TIMEOUT_CWND = 2.0

    GAIN = 3.0      # proportional gain on the queueing error, per RTT
    INC_CAP = 0.5   # max growth, packets per RTT
    DEC_CAP = 0.8   # max shrink, as a fraction of cwnd per RTT

    PROBE_INTERVAL = 80    # ticks between baseline-RTT refreshes
    PROBE_DURATION = 6     # ticks held low while the queue drains
    PROBE_CWND = 4.0       # window used during a probe
    PROBE_HISTORY = 4      # probes kept; baseline is their minimum
    LOW_CWND_THRESH = 3.0   # cwnd this low for a while suggests a stale baseline
    LOW_CWND_PATIENCE = 16  # consecutive ticks at/below that before forcing a probe

    def __init__(self, config: dict):
        self.lo = float(config["min_cwnd"])
        self.hi = float(config["max_cwnd"])
        self.cwnd = float(config["initial_cwnd"])
        self.min_rtt: float | None = None
        self.probe_history: deque[float] = deque(maxlen=self.PROBE_HISTORY)
        self.loss_ewma = 0.0
        self.loss_streak = 0
        self.slow_start = True
        self.low_ticks = 0

        self.probing = False
        self.probe_end_t = 0
        self.probe_min_rtt = float("inf")
        self.pre_probe_cwnd = self.cwnd
        self.next_probe_t = self.PROBE_INTERVAL

    def tick(self, obs: dict) -> dict:
        t = int(obs["t"])
        acked = float(obs["acked"])
        lost = float(obs["lost"])
        rtt = float(obs["rtt"])
        timeout = bool(obs["timeout"])

        if self.min_rtt is None:
            self.min_rtt = rtt

        sent = acked + lost
        p = lost / sent if sent > 0 else 0.0
        self.loss_ewma = (1 - self.LOSS_EWMA_K) * self.loss_ewma + self.LOSS_EWMA_K * p
        self.loss_streak = self.loss_streak + 1 if lost > 0 else 0

        if timeout:
            self.cwnd = min(self.cwnd, self.TIMEOUT_CWND)
            self.slow_start = True
            self.low_ticks = 0
            self.loss_streak = 0
            self.probing = False
            self.next_probe_t = t + self.PROBE_INTERVAL
            self.cwnd = min(self.hi, max(self.lo, self.cwnd))
            return self._out(rtt, 0.0, "timeout")

        if self.probing:
            self.probe_min_rtt = min(self.probe_min_rtt, rtt)
            self.cwnd = self.PROBE_CWND
            if t >= self.probe_end_t:
                # A single probe's RTT can be inflated by a flow sharing the
                # link if its queue happens to be nonempty right then, so the
                # baseline is the minimum over several recent probes rather
                # than just the latest one.
                self.probe_history.append(self.probe_min_rtt)
                new_min_rtt = min(self.probe_history)
                # A big change from the old baseline (either direction) means
                # the old equilibrium window is no longer the right target,
                # so re-run slow start to find the new one quickly rather
                # than crawling there via the additive increase.
                if self.min_rtt and abs(new_min_rtt - self.min_rtt) > 0.5 * self.min_rtt:
                    self.slow_start = True
                self.min_rtt = new_min_rtt
                self.cwnd = self.pre_probe_cwnd
                self.probing = False
                self.low_ticks = 0
                self.next_probe_t = t + self.PROBE_INTERVAL
            self.cwnd = min(self.hi, max(self.lo, self.cwnd))
            return self._out(rtt, 0.0, "probe")

        if not self.slow_start:
            if self.cwnd <= self.LOW_CWND_THRESH:
                self.low_ticks += 1
            else:
                self.low_ticks = 0

        if not self.slow_start and (t >= self.next_probe_t or self.low_ticks >= self.LOW_CWND_PATIENCE):
            self.probing = True
            self.probe_min_rtt = rtt
            self.pre_probe_cwnd = self.cwnd
            self.probe_end_t = t + self.PROBE_DURATION
            self.cwnd = min(self.hi, max(self.lo, self.PROBE_CWND))
            return self._out(rtt, 0.0, "probe")

        diff = self.cwnd * max(0.0, rtt - self.min_rtt) / rtt if rtt > 0 else 0.0

        if self.slow_start and (diff > self.SS_EXIT_DIFF or self.loss_streak >= self.SS_EXIT_LOSS_STREAK):
            self.slow_start = False
            self.next_probe_t = t + self.PROBE_INTERVAL

        if self.slow_start:
            self.cwnd += acked
        else:
            rate = acked / self.cwnd if self.cwnd > 0 else 0.0
            delay_term = max(-self.DEC_CAP * self.cwnd,
                              min(self.INC_CAP, self.GAIN * (self.TARGET - diff)))
            loss_drag = self.LOSS_GAIN * self.loss_ewma * self.cwnd
            self.cwnd += rate * (delay_term - loss_drag)

        self.cwnd = min(self.hi, max(self.lo, self.cwnd))
        mode = "slow_start" if self.slow_start else "ca"
        return self._out(rtt, diff, mode)

    def _out(self, rtt: float, diff: float, mode: str) -> dict:
        return {
            "cwnd": self.cwnd,
            "telemetry": {
                "cwnd": self.cwnd,
                "rtt": rtt,
                "min_rtt": self.min_rtt if self.min_rtt is not None else rtt,
                "diff": diff,
                "loss_ewma": self.loss_ewma,
                "mode": mode,
            },
        }
