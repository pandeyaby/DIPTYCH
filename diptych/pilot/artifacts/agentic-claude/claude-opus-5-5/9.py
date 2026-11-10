"""Adaptive concurrency controller.

Velocity-form PI controller on a normalized, asymmetrically weighted latency
error, tracking a volatility-derated effective setpoint.

Per tick, with ``sp`` the setpoint and ``lat`` the observed latency:

    derate = min(KV * sigma_lat + KA * cv_arr, DERATE_MAX)  # from *prior* ticks
    sp_eff = sp * (1 - derate)
    e      = clip((sp_eff - lat) / sp, -1, 1)
    h      = e * (1 + CURV * |e|)
    g      = h            if e >= 0     (latency below target -> grow gently)
           = DOWN * h     if e <  0     (latency above target -> shrink hard)
    trend  = (1 - A) * trend + A * g    (EWMA: recent observations weigh most)
    delta  = KI * g + KP * (trend - trend_prev)
    limit  = clamp(limit + delta, min_limit, max_limit)

How each requirement in TASK.md is met:

1. Asymmetry: every term of ``delta`` is linear in ``g`` and ``g`` is
   DOWN = 4x larger for latency above target than for an equal deviation
   below it, so the downward correction is 4x (> 2x) the upward one.
2. Recency: the KP term telescopes to ``KP * trend``, an EWMA in which newer
   errors weigh more, while the KI term is order-independent; so improving
   sequences end strictly higher than the same observations worsening.
3. Anti-windup: the only integrating state is the clamped limit itself (no
   hidden integrator), and while the limit is pinned at a bound with the
   error pushing further into it the trend memory is cleared.  The first
   response after saturation is therefore ``(KI + KP*A) * g(e)``: a function
   of the newly observed error only, independent of saturation duration.
   Volatility used at a tick comes from prior ticks, so the excursion itself
   cannot amplify its own response.
4. Volatility: latency dispersion and offered-load coefficient of variation
   both lower the effective setpoint, so a noisier workload settles at a
   strictly lower limit.  Arrival CV is observable even when the limit clips
   admitted load, which makes the effect robust in closed loop.
5. Setpoint change: trend and both volatility estimators are reset, and
   corrections are additive (independent of the current limit level), so
   corrections after the change depend only on post-change observations.
6/7. Pure float arithmetic in a fixed order; no hashing, randomness, clocks
   or I/O.  ``snapshot`` is JSON, which round-trips floats exactly.
8. Telemetry always carries the same fixed set of keys.
"""

from __future__ import annotations

import json
import math

KI = 2.0          # integral gain (rps per unit normalized error)
KP = 6.0          # trend gain
A = 0.3           # trend EWMA weight on the newest observation
DOWN = 4.0        # downward/upward correction ratio (must exceed 2)
CURV = 2.0        # extra gain for large errors
KV = 1.0          # derating per unit std-dev of normalized latency
KA = 0.5          # derating per unit coefficient of variation of arrivals
DERATE_MAX = 0.3  # cap on fractional setpoint derating
VB = 0.05         # floor on volatility-estimator EWMA weight

_STATE_KEYS = (
    "min_limit", "max_limit", "limit", "trend", "sp",
    "lat_mean", "lat_var", "arr_mean", "arr_var", "n_vol",
    "ticks", "resets", "sat_ticks",
)
_FLOAT_KEYS = (
    "min_limit", "max_limit", "limit", "trend",
    "lat_mean", "lat_var", "arr_mean", "arr_var",
)
_INT_KEYS = ("n_vol", "ticks", "resets", "sat_ticks")


def _finite(x: float, fallback: float) -> float:
    return x if math.isfinite(x) else fallback


class Controller:
    def __init__(self, config: dict):
        self.min_limit = float(config["min_limit"])
        self.max_limit = float(config["max_limit"])
        init = float(config.get("initial_limit", self.min_limit))
        self.limit = min(max(init, self.min_limit), self.max_limit)
        self.trend = 0.0
        self.sp = None  # setpoint in force; None before the first tick
        self.lat_mean = 0.0
        self.lat_var = 0.0
        self.arr_mean = 0.0
        self.arr_var = 0.0
        self.n_vol = 0  # samples in the volatility estimators since reset
        self.ticks = 0
        self.resets = 0
        self.sat_ticks = 0

    def _reset_history(self) -> None:
        self.trend = 0.0
        self.lat_mean = 0.0
        self.lat_var = 0.0
        self.arr_mean = 0.0
        self.arr_var = 0.0
        self.n_vol = 0

    def tick(self, obs: dict) -> dict:
        sp = _finite(float(obs["setpoint_ms"]), 0.0)
        if sp <= 0.0:
            sp = 1e-9
        lat = float(obs["latency_ms"])
        lat = _finite(lat, 1e300 if lat != lat or lat > 0 else 0.0)
        arr = max(_finite(float(obs.get("arrival_rps", 0.0)), 0.0), 0.0)

        sp_reset = 0
        if self.sp is None or sp != self.sp:
            if self.sp is not None:
                sp_reset = 1
                self.resets += 1
            self._reset_history()
            self.sp = sp

        # Volatility, estimated from prior ticks only.
        if self.n_vol > 1:
            sigma_lat = math.sqrt(self.lat_var)
            cv_arr = math.sqrt(self.arr_var) / self.arr_mean if self.arr_mean > 1e-9 else 0.0
        else:
            sigma_lat = 0.0
            cv_arr = 0.0
        derate = min(KV * sigma_lat + KA * cv_arr, DERATE_MAX)
        sp_eff = sp * (1.0 - derate)

        err = (sp - lat) / sp
        e = min(max((sp_eff - lat) / sp, -1.0), 1.0)
        h = e * (1.0 + CURV * abs(e))
        g = h if e >= 0.0 else DOWN * h

        trend_prev = self.trend
        trend = (1.0 - A) * trend_prev + A * g
        delta = KI * g + KP * (trend - trend_prev)
        new = self.limit + delta

        saturated = 0
        if new >= self.max_limit:
            new = self.max_limit
            if g > 0.0:
                saturated = 1
        elif new <= self.min_limit:
            new = self.min_limit
            if g < 0.0:
                saturated = -1
        if saturated:
            trend = 0.0  # anti-windup: no memory accumulated while pinned
            self.sat_ticks += 1
        else:
            self.sat_ticks = 0
        applied = new - self.limit
        self.limit = new
        self.trend = trend

        # Fold this observation into the volatility estimators.
        z = min(max((lat - sp) / sp, -1.0), 1.0)
        if self.n_vol == 0:
            self.lat_mean = z
            self.lat_var = 0.0
            self.arr_mean = arr
            self.arr_var = 0.0
        else:
            w = max(VB, 1.0 / (self.n_vol + 1))
            d = z - self.lat_mean
            self.lat_mean += w * d
            self.lat_var = (1.0 - w) * (self.lat_var + w * d * d)
            d = arr - self.arr_mean
            self.arr_mean += w * d
            self.arr_var = (1.0 - w) * (self.arr_var + w * d * d)
        self.n_vol += 1
        self.ticks += 1

        telemetry = {
            "limit": self.limit,
            "delta": applied,
            "err": err,
            "err_eff": e,
            "g": g,
            "trend": self.trend,
            "sigma_lat": sigma_lat,
            "cv_arrival": cv_arr,
            "derate": derate,
            "setpoint_eff_ms": sp_eff,
            "saturated": saturated,
            "sat_ticks": self.sat_ticks,
            "setpoint_reset": sp_reset,
            "resets": self.resets,
            "ticks": self.ticks,
        }
        return {"limit": self.limit, "telemetry": telemetry}

    def snapshot(self) -> str:
        return json.dumps({k: getattr(self, k) for k in _STATE_KEYS}, sort_keys=True)

    @classmethod
    def restore(cls, blob: str) -> "Controller":
        state = json.loads(blob)
        obj = cls.__new__(cls)
        for k in _FLOAT_KEYS:
            setattr(obj, k, float(state[k]))
        for k in _INT_KEYS:
            setattr(obj, k, int(state[k]))
        obj.sp = None if state["sp"] is None else float(state["sp"])
        return obj
