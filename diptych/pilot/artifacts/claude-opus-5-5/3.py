"""Adaptive concurrency controller.

The limit is updated multiplicatively (in log space) from three terms:

* integral:   asymmetric per-sample step on the normalized error; downward
              gain far exceeds upward gain (requirement 1).  Because the
              per-sample steps commute, order-dependence comes only from:
* derivative: a step proportional to the change in a recency-weighted EWMA of
              the error.  It telescopes to k * (s_last - s_first), so recent
              observations dominate (requirement 2).
* volatility: a steady penalty proportional to the EW standard deviation of
              the error, pulling the equilibrium lower on noisier workloads
              (requirement 4, reinforced by the asymmetric integral).

The limit itself is the only accumulator and it is hard-clamped each tick,
so saturation cannot wind up (requirement 3).  Filter state is reset when
the setpoint changes (requirement 5).  All arithmetic is plain float math
with no hashing or randomness (requirement 6), and snapshots serialize every
float as a hex string so they round-trip exactly (requirement 7).
"""

import json
import math

_UP_GAIN = 0.05        # log-step per unit of negative error (latency below setpoint)
_DOWN_GAIN = 0.25      # log-step per unit of positive error (latency above setpoint)
_DERIV_GAIN = 0.3      # gain on the change in smoothed error
_VOL_GAIN = 0.02       # log-step penalty per unit of error std-dev
_ALPHA = 0.3           # EWMA smoothing factor (weight of newest observation)
_ERR_CAP = 3.0         # cap on normalized error to bound shocks
_STEP_CAP = 0.5        # cap on per-tick |log change|

_FLOAT_FIELDS = (
    "min_limit", "max_limit", "limit", "setpoint",
    "ewma", "var", "last_error", "last_integral",
    "last_deriv", "last_vol",
)
_INT_FIELDS = ("initialized", "have_setpoint", "since_change", "ticks")


class Controller:
    def __init__(self, config: dict):
        self.min_limit = float(config.get("min_limit", 1.0))
        self.max_limit = float(config.get("max_limit", 200.0))
        if self.max_limit < self.min_limit:
            self.max_limit = self.min_limit
        init = float(config.get("initial_limit", 50.0))
        self.limit = self._clamp(init)
        self.setpoint = 0.0
        self.have_setpoint = 0
        self.initialized = 0      # whether the EWMA/variance filters hold data
        self.ewma = 0.0
        self.var = 0.0
        self.since_change = 0
        self.ticks = 0
        self.last_error = 0.0
        self.last_integral = 0.0
        self.last_deriv = 0.0
        self.last_vol = 0.0

    def _clamp(self, x: float) -> float:
        if not (x == x):  # NaN guard
            return self.min_limit
        if x < self.min_limit:
            return self.min_limit
        if x > self.max_limit:
            return self.max_limit
        return x

    def tick(self, obs: dict) -> dict:
        latency = float(obs.get("latency_ms", 0.0))
        setpoint = float(obs.get("setpoint_ms", 1.0))
        if not (setpoint > 0.0):
            setpoint = 1e-9
        if not (latency == latency):
            latency = setpoint

        # Setpoint change: discard all pre-change filter history.
        if not self.have_setpoint or setpoint != self.setpoint:
            self.setpoint = setpoint
            self.have_setpoint = 1
            self.initialized = 0
            self.ewma = 0.0
            self.var = 0.0
            self.since_change = 0

        e = (latency - setpoint) / setpoint
        if e > _ERR_CAP:
            e = _ERR_CAP
        elif e < -1.0:
            e = -1.0

        # Recency-weighted filters.
        if not self.initialized:
            self.ewma = e
            self.var = 0.0
            self.initialized = 1
            deriv = 0.0
        else:
            prev = self.ewma
            dev = e - prev
            self.ewma = prev + _ALPHA * dev
            self.var = (1.0 - _ALPHA) * (self.var + _ALPHA * dev * dev)
            deriv = -_DERIV_GAIN * (self.ewma - prev)

        # Asymmetric integral step on the raw error.
        if e > 0.0:
            integral = -_DOWN_GAIN * e
        else:
            integral = -_UP_GAIN * e

        std = math.sqrt(self.var) if self.var > 0.0 else 0.0
        vol = -_VOL_GAIN * std

        step = integral + deriv + vol
        if step > _STEP_CAP:
            step = _STEP_CAP
        elif step < -_STEP_CAP:
            step = -_STEP_CAP

        # The limit is the only accumulator and is clamped every tick (anti-windup).
        self.limit = self._clamp(self.limit * math.exp(step))

        self.last_error = e
        self.last_integral = integral
        self.last_deriv = deriv
        self.last_vol = vol
        self.since_change += 1
        self.ticks += 1

        if self.limit >= self.max_limit:
            saturated = "max"
        elif self.limit <= self.min_limit:
            saturated = "min"
        else:
            saturated = "none"

        telemetry = {
            "limit": float(self.limit),
            "error": float(e),
            "ewma_error": float(self.ewma),
            "std_error": float(std),
            "setpoint_ms": float(self.setpoint),
            "integral_step": float(integral),
            "derivative_step": float(deriv),
            "volatility_step": float(vol),
            "total_step": float(step),
            "ticks_since_setpoint_change": int(self.since_change),
            "ticks": int(self.ticks),
            "saturated": saturated,
        }
        return {"limit": float(self.limit), "telemetry": telemetry}

    def snapshot(self) -> str:
        state = {"v": 1}
        for name in _FLOAT_FIELDS:
            state[name] = float(getattr(self, name)).hex()
        for name in _INT_FIELDS:
            state[name] = int(getattr(self, name))
        return json.dumps(state, sort_keys=True, separators=(",", ":"))

    @classmethod
    def restore(cls, blob: str) -> "Controller":
        state = json.loads(blob)
        obj = cls.__new__(cls)
        for name in _FLOAT_FIELDS:
            setattr(obj, name, float.fromhex(state[name]))
        for name in _INT_FIELDS:
            setattr(obj, name, int(state[name]))
        return obj
