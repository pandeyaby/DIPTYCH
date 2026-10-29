"""Deterministic bottleneck-link simulator for the congestion-control task.

Fluid, tick-based model of one drop-tail bottleneck shared by several flows.
Each tick a flow with congestion window ``w`` sends ``w / rtt`` packets, where
``rtt = base_rtt + queue / capacity`` (in ticks). Arrivals beyond the link
capacity queue up to ``buffer`` packets; the excess is dropped in proportion
to each flow's arrivals. Feedback is delivered in the same tick.

Random (non-congestive) loss is pre-drawn per tick from ``random.Random(seed)``
so two runs with the same seed see identical draws (common random numbers)
whatever the controllers do. The simulator is a plain object: forking a run
is ``copy.deepcopy`` of the ``Sim``.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

CONFIG: dict[str, float] = {"min_cwnd": 1.0, "max_cwnd": 2000.0, "initial_cwnd": 4.0}

CAPACITY = 10.0   # packets per tick
BASE_RTT = 2.0    # ticks
BUFFER = 20.0     # packets (one bandwidth-delay product)
RANDOM_LOSS_P = 0.002


@dataclass
class Path:
    capacity: float = CAPACITY
    base_rtt: float = BASE_RTT
    buffer: float = BUFFER
    blackout: bool = False      # everything sent is lost
    loss_rate: float = 0.0      # extra fraction of arrivals lost (sustained loss)


@dataclass
class Flow:
    controller: Any                      # object with tick(obs) -> {"cwnd", "telemetry"}
    start: int = 0
    size: float | None = None            # packets to deliver; None = long-lived
    cwnd: float = CONFIG["initial_cwnd"]
    delivered: float = 0.0
    done_at: int | None = None
    log: list[dict] = field(default_factory=list)


class Sim:
    def __init__(self, flows: list[Flow], path: Path | None = None, seed: int = 0, horizon: int = 2000):
        self.flows = flows
        self.path = path or Path()
        self.t = 0
        self.queue = 0.0
        self.queue_log: list[float] = []
        rng = random.Random(seed)
        self._u = [[rng.random() for _ in range(8)] for _ in range(horizon)]

    def step(self) -> None:
        p, t = self.path, self.t
        rtt = p.base_rtt + (self.queue / p.capacity if p.capacity > 0 else 0.0)
        active = [f for f in self.flows if f.start <= t and f.done_at is None]
        arrivals = {id(f): f.cwnd / rtt for f in active}
        lost = {id(f): 0.0 for f in active}
        if p.blackout:
            lost = dict(arrivals)
        else:
            # Path loss (sustained and random) happens before the bottleneck
            # queue; only what survives it competes for the link.
            offered = {}
            for f in active:
                a = arrivals[id(f)]
                pre = a * p.loss_rate
                if self._u[t][self.flows.index(f) % 8] < RANDOM_LOSS_P:
                    pre += min(1.0, a - pre)
                lost[id(f)] = pre
                offered[id(f)] = a - pre
            total_offered = sum(offered.values())
            q = self.queue + total_offered - p.capacity
            overflow = max(0.0, q - p.buffer)
            self.queue = max(0.0, q - overflow)
            for f in active:
                if total_offered > 0:
                    lost[id(f)] += overflow * offered[id(f)] / total_offered
        self.queue_log.append(self.queue)
        for f in active:
            a, l = arrivals[id(f)], lost[id(f)]
            acked = a - l
            f.delivered += acked
            obs = {"t": t, "acked": acked, "lost": l, "rtt": rtt, "timeout": bool(a > 0 and acked <= 0)}
            d = f.controller.tick(obs)
            f.cwnd = min(CONFIG["max_cwnd"], max(CONFIG["min_cwnd"], float(d["cwnd"])))
            f.log.append({"t": t, "acked": acked, "lost": l, "rtt": rtt, "cwnd": f.cwnd,
                          "telemetry": d.get("telemetry", {})})
            if f.size is not None and f.delivered >= f.size:
                f.done_at = t
        self.t += 1

    def run(self, until: int) -> "Sim":
        while self.t < until:
            self.step()
        return self
