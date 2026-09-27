"""Deterministic service plant for the pilot study.

The plant is a pure function of ``(scenario, seed, t, previous limit,
var_scale)``: noise is pre-drawn per tick from ``random.Random(seed)``, so two
runs with the same seed consume identical draws (common random numbers) no
matter what the controller does. Forking a run therefore needs only the
controller's state, never the plant's.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

CONFIG: dict[str, float] = {"min_limit": 1.0, "max_limit": 200.0, "initial_limit": 50.0}

BASE_MS = 40.0
CAPACITY = 100.0


@dataclass(frozen=True)
class Scenario:
    name: str
    length: int
    mean_rps: tuple[tuple[int, float], ...]  # (from_tick, value) steps
    sd_rps: tuple[tuple[int, float], ...]
    setpoint_ms: tuple[tuple[int, float], ...]

    def _at(self, steps: tuple[tuple[int, float], ...], t: int) -> float:
        v = steps[0][1]
        for start, val in steps:
            if t >= start:
                v = val
        return v

    def mean(self, t: int) -> float:
        return self._at(self.mean_rps, t)

    def sd(self, t: int) -> float:
        return self._at(self.sd_rps, t)

    def setpoint(self, t: int) -> float:
        return self._at(self.setpoint_ms, t)


SCENARIOS: dict[str, Scenario] = {
    "steady": Scenario("steady", 240, ((0, 85.0),), ((0, 10.0),), ((0, 100.0),)),
    # Same parameters as "steady", longer horizon; draws for t < 240 coincide.
    "steady_long": Scenario("steady_long", 400, ((0, 85.0),), ((0, 10.0),), ((0, 100.0),)),
    "volatile_step": Scenario("volatile_step", 240, ((0, 85.0),), ((0, 5.0), (120, 20.0)), ((0, 100.0),)),
    "setpoint_step": Scenario("setpoint_step", 240, ((0, 85.0),), ((0, 8.0),), ((0, 100.0), (120, 70.0))),
    "saturate": Scenario("saturate", 240, ((0, 30.0), (140, 110.0)), ((0, 4.0),), ((0, 100.0),)),
}


def latency_ms(admitted: float) -> float:
    u = min(max(admitted, 0.0) / CAPACITY, 0.98)
    return BASE_MS * (1.0 + 0.73 * u ** 4 / (1.0 - u))


@dataclass
class Plant:
    scenario: Scenario
    seed: int = 0
    var_scale: float = 1.0
    _z: list[tuple[float, float]] = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        rng = random.Random(self.seed)
        self._z = [(rng.gauss(0.0, 1.0), rng.gauss(0.0, 1.0)) for _ in range(self.scenario.length)]

    def observe(self, t: int, prev_limit: float) -> dict[str, float]:
        z_load, z_lat = self._z[t]
        sc = self.scenario
        arrivals = max(0.0, sc.mean(t) + sc.sd(t) * self.var_scale * z_load)
        admitted = min(arrivals, prev_limit)
        lat = latency_ms(admitted) * (1.0 + 0.02 * z_lat)
        return {
            "t": t,
            "latency_ms": lat,
            "setpoint_ms": sc.setpoint(t),
            "arrival_rps": arrivals,
            "admitted_rps": admitted,
        }
