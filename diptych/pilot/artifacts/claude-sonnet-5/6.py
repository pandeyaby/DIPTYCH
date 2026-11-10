"""Adaptive concurrency controller.

A closed-loop limiter that adjusts a concurrency limit each tick based on
observed latency relative to a setpoint. Uses an exponentially-weighted
moving average (not an unbounded integrator) for the error signal so that
anti-windup falls out naturally, plus an explicit conditional-freeze /
reset rule around saturation boundaries, asymmetric up/down gains, and a
volatility penalty derived from a running variance estimate.
"""

import json
import math


class Controller:
    _ALPHA = 0.4          # EWMA decay for the error signal (recency weighting)
    _ALPHA_VAR = 0.3      # EWMA decay for the variance estimate
    _DOWN_GAIN = 0.6       # gain applied when latency is above setpoint
    _UP_GAIN = 0.2         # gain applied when latency is below setpoint
    _VOL_GAIN = 2.0        # strength of the volatility suppression penalty
    _FACTOR_MIN = 0.2
    _FACTOR_MAX = 2.0
    _REL_ERROR_CLIP = 5.0
    _EPS = 1e-9

    def __init__(self, config: dict):
        self.min_limit = float(config.get("min_limit", 1.0))
        self.max_limit = float(config.get("max_limit", 200.0))
        initial_limit = float(config.get("initial_limit", 50.0))
        self.limit = min(max(initial_limit, self.min_limit), self.max_limit)
        self.ewma_error = None
        self.ewma_var = 0.0
        self.last_setpoint = None

    def tick(self, obs: dict) -> dict:
        t = int(obs["t"])
        latency_ms = float(obs["latency_ms"])
        setpoint_ms = float(obs["setpoint_ms"])

        rel_error = (latency_ms - setpoint_ms) / setpoint_ms
        if rel_error > self._REL_ERROR_CLIP:
            rel_error = self._REL_ERROR_CLIP
        elif rel_error < -self._REL_ERROR_CLIP:
            rel_error = -self._REL_ERROR_CLIP

        setpoint_changed = (
            self.last_setpoint is not None and setpoint_ms != self.last_setpoint
        )

        at_max = self.limit >= self.max_limit - self._EPS
        at_min = self.limit <= self.min_limit + self._EPS
        wants_increase = rel_error < 0.0
        wants_decrease = rel_error > 0.0

        if self.ewma_error is None or setpoint_changed:
            # Fresh start, or the target moved: history before this point
            # must not influence future corrections.
            self.ewma_error = rel_error
            self.ewma_var = 0.0
        elif at_max and wants_increase:
            # Saturated and still pushing the same direction: freeze the
            # error estimate instead of letting it accumulate (anti-windup).
            pass
        elif at_min and wants_decrease:
            pass
        elif (at_max and wants_decrease) or (at_min and wants_increase):
            # Breaking out of saturation: respond to the immediately
            # observed error only, not to however long we were pinned.
            self.ewma_error = rel_error
            self.ewma_var = 0.0
        else:
            delta = rel_error - self.ewma_error
            self.ewma_var = (
                (1.0 - self._ALPHA_VAR) * self.ewma_var
                + self._ALPHA_VAR * (delta * delta)
            )
            self.ewma_error = (
                (1.0 - self._ALPHA) * self.ewma_error + self._ALPHA * rel_error
            )

        smoothed = self.ewma_error
        if smoothed > 0.0:
            factor = 1.0 - self._DOWN_GAIN * smoothed
        else:
            factor = 1.0 + self._UP_GAIN * (-smoothed)

        if factor < self._FACTOR_MIN:
            factor = self._FACTOR_MIN
        elif factor > self._FACTOR_MAX:
            factor = self._FACTOR_MAX

        ewma_std = math.sqrt(self.ewma_var) if self.ewma_var > 0.0 else 0.0
        volatility_adjustment = 1.0 / (1.0 + self._VOL_GAIN * ewma_std)

        raw_new_limit = self.limit * factor * volatility_adjustment

        new_limit = raw_new_limit
        if new_limit < self.min_limit:
            new_limit = self.min_limit
        elif new_limit > self.max_limit:
            new_limit = self.max_limit

        self.limit = new_limit
        self.last_setpoint = setpoint_ms

        saturated = 1 if (
            new_limit >= self.max_limit - self._EPS
            or new_limit <= self.min_limit + self._EPS
        ) else 0

        telemetry = {
            "t": t,
            "limit": new_limit,
            "rel_error": rel_error,
            "smoothed_error": smoothed,
            "ewma_std": ewma_std,
            "factor": factor,
            "volatility_adjustment": volatility_adjustment,
            "saturated": saturated,
            "setpoint_changed": 1 if setpoint_changed else 0,
        }

        return {"limit": new_limit, "telemetry": telemetry}

    def snapshot(self) -> str:
        state = {
            "min_limit": self.min_limit,
            "max_limit": self.max_limit,
            "limit": self.limit,
            "ewma_error": self.ewma_error,
            "ewma_var": self.ewma_var,
            "last_setpoint": self.last_setpoint,
        }
        return json.dumps(state, sort_keys=True)

    @classmethod
    def restore(cls, blob: str) -> "Controller":
        state = json.loads(blob)
        obj = cls.__new__(cls)
        obj.min_limit = float(state["min_limit"])
        obj.max_limit = float(state["max_limit"])
        obj.limit = float(state["limit"])
        obj.ewma_error = state["ewma_error"]
        if obj.ewma_error is not None:
            obj.ewma_error = float(obj.ewma_error)
        obj.ewma_var = float(state["ewma_var"])
        obj.last_setpoint = state["last_setpoint"]
        if obj.last_setpoint is not None:
            obj.last_setpoint = float(obj.last_setpoint)
        return obj
