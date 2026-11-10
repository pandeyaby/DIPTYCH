"""Adaptive concurrency controller.

Design
------
* Relative error ``e = (sp_eff - latency) / setpoint`` (positive = headroom),
  clipped to [-1, 1] so equal-magnitude deviations are treated equally.
* Asymmetry: negative (over-setpoint) errors are multiplied by ``ASYM`` (3x),
  so a downward correction is three times the upward one for an equal
  deviation (requirement 1).
* Base limit: multiplicative integrator ``base *= 1 + G * ea``. A product of
  per-tick factors is order independent, so ordering effects come only from:
* Trend term: output ``limit = base * (1 + K * ewma(ea))``. The EWMA weights
  recent observations more, so an improving run ends higher than the same
  observations in worsening order (requirement 2).
* Volatility suppression: the effective setpoint is lowered in proportion to
  the observed coefficient of variation of arrivals and the relative spread of
  latency, so noisier workloads converge to a lower limit (requirement 4).
* Anti-windup: while the emitted limit is pinned at ``max_limit`` the base is
  clamped and the trend / volatility filters are reset, so the first response
  to an excursion depends only on that tick's error (requirement 3).
* Setpoint change: the last emitted limit is folded into the base and every
  filter is reset, so corrections afterwards depend only on post-change
  observations (requirement 5).
* State is plain floats serialised as JSON (repr-exact), no randomness, no
  hash-order dependence (requirements 6, 7). Telemetry keys are fixed (8).
"""

from __future__ import annotations

import json

ASYM = 3.0       # downward / upward gain ratio (> 2 required)
G = 0.08         # integrator gain
K = 0.3          # trend (recency) gain on the output
A = 0.3          # trend EWMA weight on newest observation
V = 0.1          # volatility EWMA weight
C_ARR = 0.3      # setpoint shrink per unit arrival coefficient of variation
C_LAT = 0.3      # setpoint shrink per unit relative latency spread
MAX_SHRINK = 0.3

_STATE_KEYS = (
    "min_limit", "max_limit", "base", "limit", "trend", "last_setpoint",
    "arr_n", "arr_mean", "arr_var", "lat_n", "lat_mean", "lat_var",
    "saturated", "ticks",
)


class Controller:
    def __init__(self, config: dict):
        self.min_limit = float(config.get("min_limit", 1.0))
        self.max_limit = float(config.get("max_limit", 200.0))
        init = float(config.get("initial_limit", 50.0))
        init = min(max(init, self.min_limit), self.max_limit)
        self.base = init
        self.limit = init
        self.trend = 0.0
        self.last_setpoint = -1.0  # sentinel: no setpoint seen yet
        self.saturated = 0
        self.ticks = 0
        self._reset_stats()

    # ------------------------------------------------------------------ helpers
    def _reset_stats(self) -> None:
        self.arr_n = 0
        self.arr_mean = 0.0
        self.arr_var = 0.0
        self.lat_n = 0
        self.lat_mean = 0.0
        self.lat_var = 0.0

    @staticmethod
    def _ewma_update(n, mean, var, x):
        if n == 0:
            return 1, x, 0.0
        d = x - mean
        mean = mean + V * d
        var = (1.0 - V) * (var + V * d * d)
        return n + 1, mean, var

    def _clamp(self, x: float) -> float:
        return min(max(x, self.min_limit), self.max_limit)

    # --------------------------------------------------------------------- tick
    def tick(self, obs: dict) -> dict:
        lat = float(obs["latency_ms"])
        sp = float(obs["setpoint_ms"])
        arr = float(obs.get("arrival_rps", 0.0))
        if not sp > 0.0:
            sp = 1e-9
        if lat != lat:  # NaN guard
            lat = sp

        sp_changed = 0
        if self.last_setpoint >= 0.0 and sp != self.last_setpoint:
            # Fold the emitted limit into the base, drop all pre-change history.
            sp_changed = 1
            self.base = self.limit
            self.trend = 0.0
            self.saturated = 0
            self._reset_stats()
        self.last_setpoint = sp

        # Volatility from history *before* this observation.
        arr_cv = 0.0
        if self.arr_n >= 2 and self.arr_mean > 1e-9:
            arr_cv = (self.arr_var ** 0.5) / self.arr_mean
        lat_sd = 0.0
        if self.lat_n >= 2:
            lat_sd = (self.lat_var ** 0.5)
        shrink = min(C_ARR * arr_cv + C_LAT * lat_sd, MAX_SHRINK)
        sp_eff = sp * (1.0 - shrink)

        e = (sp_eff - lat) / sp
        e = min(max(e, -1.0), 1.0)
        ea = e if e >= 0.0 else ASYM * e

        # Update volatility statistics with the current observation.
        self.arr_n, self.arr_mean, self.arr_var = self._ewma_update(
            self.arr_n, self.arr_mean, self.arr_var, arr)
        self.lat_n, self.lat_mean, self.lat_var = self._ewma_update(
            self.lat_n, self.lat_mean, self.lat_var, (lat - sp) / sp)

        self.base = self._clamp(self.base * (1.0 + G * ea))
        self.trend = (1.0 - A) * self.trend + A * ea
        factor = max(1.0 + K * self.trend, 0.1)
        limit = self._clamp(self.base * factor)

        if limit >= self.max_limit:
            # Anti-windup: pin state so saturation duration leaves no memory.
            self.base = self.max_limit
            self.trend = 0.0
            self._reset_stats()
            self.saturated = 1
        elif limit <= self.min_limit:
            self.base = self.min_limit
            self.trend = 0.0
            self.saturated = -1
        else:
            self.saturated = 0

        self.limit = limit
        self.ticks += 1
        telemetry = {
            "error": e,
            "error_asym": ea,
            "trend": self.trend,
            "base_limit": self.base,
            "effective_setpoint_ms": sp_eff,
            "volatility_shrink": shrink,
            "arrival_cv": arr_cv,
            "latency_spread": lat_sd,
            "saturated": self.saturated,
            "setpoint_changed": sp_changed,
            "ticks": self.ticks,
        }
        return {"limit": limit, "telemetry": telemetry}

    # ------------------------------------------------------------ persistence
    def snapshot(self) -> str:
        return json.dumps({k: getattr(self, k) for k in _STATE_KEYS},
                          sort_keys=True)

    @classmethod
    def restore(cls, blob: str) -> "Controller":
        data = json.loads(blob)
        obj = cls.__new__(cls)
        for k in _STATE_KEYS:
            setattr(obj, k, data[k])
        return obj
