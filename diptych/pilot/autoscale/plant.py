"""Deterministic replica-set simulator for the autoscaling task.

One tick is one autoscaler sync period (15 s). A service runs ``n`` pods; a
pod serves up to ``POD_CAPACITY`` requests per tick and is not ready (serves
nothing, reports no metric) for ``STARTUP`` ticks after it is created. Each
tick the autoscaler observes every pod's readiness and CPU utilization and
returns the desired replica count, which takes effect immediately (new pods
start, surplus pods are removed newest first).

Utilization is demand / ready capacity, reported per pod with relative
measurement noise and capped at ``UTIL_CAP``; a ready pod's metric can be
missing. Noise, missing-metric draws and demand noise are pre-drawn from
``random.Random(seed)`` per (tick, pod slot), so two runs with the same seed
see identical draws whatever the autoscaler does (common random numbers).
Forking a run is ``copy.deepcopy`` of the ``Sim``.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Any

CONFIG: dict[str, float] = {
    "min_replicas": 1, "max_replicas": 60, "initial_replicas": 10,
    "target_utilization": 0.6, "tolerance": 0.1, "stabilization_ticks": 20,
}

POD_CAPACITY = 100.0   # requests per tick at utilization 1.0
STARTUP = 3            # ticks before a new pod is ready
UTIL_CAP = 1.5         # reported utilization saturates here
MAX_PODS = 64          # pre-drawn pod slots


@dataclass
class Workload:
    """Demand (requests per tick) as steps plus an optional sinusoid."""

    steps: list[tuple[int, float]] = field(default_factory=lambda: [(0, 600.0)])
    amplitude: float = 0.0
    period: int = 200
    demand_noise: float = 0.0     # relative sd of per-tick demand noise
    metric_noise: float = 0.03    # relative sd of each pod's reported utilization
    missing: float = 0.0          # probability a ready pod's metric is missing
    scale: float = 1.0            # multiplies all demand

    def level(self, t: int) -> float:
        lvl = self.steps[0][1]
        for t0, v in self.steps:
            if t >= t0:
                lvl = v
        if self.amplitude:
            lvl += self.amplitude * math.sin(2 * math.pi * t / self.period)
        return max(0.0, lvl) * self.scale


class Sim:
    def __init__(self, controller: Any, workload: Workload | None = None, seed: int = 0,
                 config: dict | None = None, horizon: int = 1500, startup: int = STARTUP):
        self.ctrl = controller
        self.w = workload or Workload()
        self.cfg = dict(CONFIG, **(config or {}))
        self.t = 0
        self.startup = startup
        self.ages = [startup] * int(self.cfg["initial_replicas"])   # initial pods are ready
        self.log: list[dict] = []
        rng = random.Random(seed)
        self._z = [[rng.gauss(0.0, 1.0) for _ in range(MAX_PODS)] for _ in range(horizon)]
        self._m = [[rng.random() for _ in range(MAX_PODS)] for _ in range(horizon)]
        self._d = [rng.gauss(0.0, 1.0) for _ in range(horizon)]

    def demand(self, t: int) -> float:
        return max(0.0, self.w.level(t) * (1.0 + self.w.demand_noise * self._d[t]))

    def observe(self) -> dict:
        """The observation for the current tick (does not advance time)."""
        t = self.t
        ready = sum(a >= self.startup for a in self.ages)
        true_u = self.demand(t) / (ready * POD_CAPACITY) if ready else UTIL_CAP
        pods = []
        for i, age in enumerate(self.ages):
            if age < self.startup:
                pods.append({"ready": False, "utilization": None})
            elif self._m[t][i] < self.w.missing:
                pods.append({"ready": True, "utilization": None})
            else:
                u = true_u * (1.0 + self.w.metric_noise * self._z[t][i])
                pods.append({"ready": True, "utilization": min(UTIL_CAP, max(0.0, u))})
        return {"t": t, "replicas": len(self.ages), "pods": pods}

    def step(self, obs: dict | None = None) -> int:
        """Advance one tick; ``obs`` overrides what the autoscaler sees."""
        t = self.t
        seen = obs if obs is not None else self.observe()
        ready = sum(a >= self.startup for a in self.ages)
        demand = self.demand(t)
        served = min(demand, ready * POD_CAPACITY)
        d = self.ctrl.tick(seen)
        lo, hi = int(self.cfg["min_replicas"]), int(self.cfg["max_replicas"])
        n = min(hi, max(lo, int(d["replicas"])))
        before = len(self.ages)
        if n > before:
            self.ages += [0] * (n - before)
        elif n < before:
            # Remove not-ready pods first, then the newest ready ones.
            self.ages.sort(reverse=True)
            self.ages = self.ages[:n]
        self.log.append({"t": t, "demand": demand, "served": served, "dropped": demand - served,
                         "ready": ready, "replicas_before": before, "replicas": n,
                         "utilization": demand / (ready * POD_CAPACITY) if ready else UTIL_CAP,
                         "obs": seen, "telemetry": d.get("telemetry", {})})
        self.ages = [a + 1 for a in self.ages]
        self.t += 1
        return n

    def run(self, until: int) -> "Sim":
        while self.t < until:
            self.step()
        return self
