"""Adaptive concurrency controller.

Keeps observed latency near a (possibly changing) setpoint by adjusting a
concurrency limit, using a recency-weighted error signal, an asymmetric
proportional response, a volatility penalty, and an anti-windup guard on
the immediate correction.
"""

import json
import math

# Weight given to the newest sample when folding it into the running
# (recency-weighted) mean/variance of the relative error. Larger == more
# recency weight, smaller == more memory of older samples.
_ALPHA_ERR = 0.35
_ALPHA_VAR = 0.35

# Proportional gains. GAIN_DOWN must stay strictly more than twice GAIN_UP
# so that closing an above-setpoint gap is always a stronger reaction than
# opening into an equal-and-opposite below-setpoint gap.
_GAIN_DOWN = 0.8
_GAIN_UP = 0.2

# How strongly latency volatility (variance of relative error) taxes the
# limit. Applied every tick regardless of direction, so it never changes
# the GAIN_DOWN / GAIN_UP ratio above.
_VOL_GAIN = 0.5


def _clamp(value, lo, hi):
    return lo if value < lo else hi if value > hi else value


class Controller:
    def __init__(self, config: dict):
        self.min_limit = float(config.get("min_limit", 1.0))
        self.max_limit = float(config.get("max_limit", 200.0))
        initial_limit = float(config.get("initial_limit", 50.0))

        self.limit = _clamp(initial_limit, self.min_limit, self.max_limit)
        self.ewma_err = 0.0
        self.ewma_var = 0.0
        self.last_setpoint = None

    def tick(self, obs: dict) -> dict:
        t = obs["t"]
        latency_ms = float(obs["latency_ms"])
        setpoint_ms = float(obs["setpoint_ms"])
        arrival_rps = float(obs["arrival_rps"])
        admitted_rps = float(obs["admitted_rps"])

        # A setpoint change invalidates the accumulated error/variance
        # history: corrections must only reflect observations after it.
        if self.last_setpoint is not None and setpoint_ms != self.last_setpoint:
            self.ewma_err = 0.0
            self.ewma_var = 0.0
        self.last_setpoint = setpoint_ms

        denom = setpoint_ms if setpoint_ms > 1e-9 else 1e-9
        error = (latency_ms - setpoint_ms) / denom

        prev_mean = self.ewma_err
        self.ewma_err = _ALPHA_ERR * error + (1.0 - _ALPHA_ERR) * prev_mean
        diff = error - prev_mean
        self.ewma_var = _ALPHA_VAR * (diff * diff) + (1.0 - _ALPHA_VAR) * self.ewma_var

        signal = self.ewma_err
        if signal >= 0.0:
            base_delta = -_GAIN_DOWN * signal
        else:
            base_delta = -_GAIN_UP * signal

        # Anti-windup: whatever the trend signal accumulated while
        # saturated, a single tick's correction can never exceed what the
        # immediate observed error alone would justify.
        if base_delta < 0.0:
            cap = _GAIN_DOWN * abs(error)
            base_delta = max(base_delta, -cap)
        else:
            cap = _GAIN_UP * abs(error)
            base_delta = min(base_delta, cap)

        vol_factor = 1.0 / (1.0 + _VOL_GAIN * math.sqrt(max(self.ewma_var, 0.0)))

        new_limit = self.limit * (1.0 + base_delta) * vol_factor
        new_limit = _clamp(new_limit, self.min_limit, self.max_limit)
        self.limit = new_limit

        saturated = 1 if (new_limit <= self.min_limit or new_limit >= self.max_limit) else 0

        telemetry = {
            "limit": new_limit,
            "error": error,
            "ewma_err": self.ewma_err,
            "ewma_var": self.ewma_var,
            "base_delta": base_delta,
            "vol_factor": vol_factor,
            "saturated": saturated,
            "t": int(t),
            "arrival_rps": arrival_rps,
            "admitted_rps": admitted_rps,
        }

        return {"limit": new_limit, "telemetry": telemetry}

    def snapshot(self) -> str:
        state = {
            "min_limit": self.min_limit,
            "max_limit": self.max_limit,
            "limit": self.limit,
            "ewma_err": self.ewma_err,
            "ewma_var": self.ewma_var,
            "last_setpoint": self.last_setpoint,
        }
        return json.dumps(state, sort_keys=True)

    @classmethod
    def restore(cls, blob: str) -> "Controller":
        state = json.loads(blob)
        obj = cls(
            {
                "min_limit": state["min_limit"],
                "max_limit": state["max_limit"],
                "initial_limit": state["limit"],
            }
        )
        obj.limit = state["limit"]
        obj.ewma_err = state["ewma_err"]
        obj.ewma_var = state["ewma_var"]
        obj.last_setpoint = state["last_setpoint"]
        return obj
