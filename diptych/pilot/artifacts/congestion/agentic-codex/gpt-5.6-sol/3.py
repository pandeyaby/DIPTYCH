"""A small-delay, loss-tolerant congestion controller.

The controller is deliberately based only on signals in the assignment's
interface.  It uses the minimum RTT as the propagation-delay estimate and
controls the flow's estimated contribution to the bottleneck queue.  Loss is
treated as congestion only when it is persistent or accompanied by excess
delay; this avoids Reno's poor behaviour on paths with occasional random
loss.
"""

from __future__ import annotations

import math
from collections import deque


class Controller:
    """Delay-targeted AIMD with persistent-loss and timeout safeguards."""

    def __init__(self, config: dict):
        self.lo = float(config["min_cwnd"])
        self.hi = float(config["max_cwnd"])
        self.cwnd = min(self.hi, max(self.lo, float(config["initial_cwnd"])))

        self.base_rtt: float | None = None
        self.rtts: deque[float] = deque(maxlen=48)
        self.previous_rtt: float | None = None
        self.new_floor_ticks = 0
        self.new_floor_candidate: float | None = None

        self.startup = True
        self.loss_ewma = 0.0
        self.loss_weight = 0.0
        self.recover_until = -1

        # A flow aims to own this many packets in the queue.  This is large
        # enough not to under-run a fluid link and small relative to the
        # supplied one-BDP buffer.
        self.queue_target = 3.0
        self.queue_high = 4.0

    def _learn_base_rtt(self, rtt: float, lost: float) -> None:
        if self.base_rtt is None or rtt < self.base_rtt:
            self.base_rtt = rtt

        self.rtts.append(rtt)

        # Start re-learning only after an abrupt RTT step.  Merely observing
        # a long-lived queue is not evidence that propagation delay changed:
        # accepting it as such would create a positive feedback loop.
        assert self.base_rtt is not None
        if (self.previous_rtt is not None and
                rtt > 1.45 * self.previous_rtt and
                self.previous_rtt < 1.55 * self.base_rtt and lost <= 0.0):
            self.new_floor_candidate = rtt
            self.new_floor_ticks = 1
        elif self.new_floor_candidate is not None:
            if rtt > 1.35 * self.base_rtt and lost <= 0.0:
                self.new_floor_ticks += 1
            else:
                self.new_floor_candidate = None
                self.new_floor_ticks = 0

        if self.new_floor_ticks >= 20:
            recent = list(self.rtts)[-20:]
            floor = min(recent)
            if floor > 1.30 * self.base_rtt:
                self.base_rtt = floor
                self.new_floor_candidate = None
                self.new_floor_ticks = 0
                self.startup = False
        self.previous_rtt = rtt

    def tick(self, obs: dict) -> dict:
        t = int(obs["t"])
        acked = max(0.0, float(obs["acked"]))
        lost = max(0.0, float(obs["lost"]))
        rtt = max(1.0e-9, float(obs["rtt"]))
        timeout = bool(obs["timeout"])

        self._learn_base_rtt(rtt, lost)
        base = self.base_rtt if self.base_rtt is not None else rtt
        queued = self.cwnd * max(0.0, rtt - base) / rtt

        sent = acked + lost
        sample_loss = lost / sent if sent > 0.0 else 0.0
        # Roughly a two-RTT moving measure, independent enough of tick length
        # to work across the tested propagation delays.
        weight = min(0.5, 1.0 / max(2.0, 2.0 * rtt))
        self.loss_ewma = (1.0 - weight) * self.loss_ewma + weight * sample_loss
        self.loss_weight = min(1.0, self.loss_weight + weight)

        if timeout:
            self.cwnd = min(2.0, max(self.lo, 1.0))
            self.startup = False
            self.recover_until = t + max(1, math.ceil(rtt))
        elif self.loss_weight >= 0.75 and self.loss_ewma >= 0.08:
            # Persistent high loss is a rate signal even if RTT is low (for
            # example policing before the bottleneck).  Repeated reductions
            # continue until the offered rate has materially fallen.
            factor = max(0.55, 1.0 - 1.5 * self.loss_ewma)
            self.cwnd *= factor
            self.startup = False
        elif lost > 0.0 and queued > self.queue_high and t >= self.recover_until:
            # Overflow: back off once per RTT, as conventional algorithms do.
            self.cwnd *= 0.7
            self.startup = False
            self.recover_until = t + max(1, math.ceil(rtt))
        elif self.startup:
            if queued >= self.queue_target:
                self.startup = False
            else:
                # ACK clocked slow start.
                self.cwnd += acked

        if not timeout and not self.startup and not (
            self.loss_weight >= 0.75 and self.loss_ewma >= 0.08
        ):
            # Additive increase below the target gives equal flows equal
            # increments.  Above it, reduce in proportion to excess queue;
            # this is faster than classic Vegas after a capacity decrease but
            # remains smooth enough not to empty the link.
            if queued < self.queue_target:
                self.cwnd += acked / max(self.cwnd, 1.0)
            elif queued > self.queue_high:
                self.cwnd -= min(0.5, 0.10 * (queued - self.queue_high))

        self.cwnd = min(self.hi, max(self.lo, self.cwnd))
        return {
            "cwnd": float(self.cwnd),
            "telemetry": {
                "cwnd": float(self.cwnd),
                "base_rtt": float(base),
                "queue_est": float(queued),
                "loss_ewma": float(self.loss_ewma),
                "mode": "startup" if self.startup else "delay_control",
            },
        }
