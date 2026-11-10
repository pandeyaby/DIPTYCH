"""Adaptive concurrency controller.

An EWMA-driven, asymmetric (AIMD-style) controller that adjusts a
concurrency limit so that observed latency tracks a (possibly changing)
setpoint, while being resistant to integrator windup and penalizing
volatile workloads.
"""

import json
import math


class Controller:
    # Fixed algorithm constants (not part of the external config contract).
    _ALPHA_ERR = 0.3        # EWMA decay for the error signal (recency weighting)
    _ALPHA_VAR = 0.3        # EWMA decay for the variance (volatility) signal
    _GAIN_DOWN = 0.8        # multiplicative gain applied when latency > setpoint
    _GAIN_UP = 0.3          # multiplicative gain applied when latency < setpoint
    _KV = 5.0               # volatility penalty strength (suppresses upward gain)

    _SCHEMA_VERSION = 1

    def __init__(self, config: dict):
        self.min_limit = float(config["min_limit"])
        self.max_limit = float(config["max_limit"])
        self.limit = float(config["initial_limit"])
        self.limit = min(max(self.limit, self.min_limit), self.max_limit)

        self._ewma_err = 0.0
        self._ewma_var = 0.0
        self._last_setpoint = None

    def tick(self, obs: dict) -> dict:
        setpoint_ms = float(obs["setpoint_ms"])
        latency_ms = float(obs["latency_ms"])

        if self._last_setpoint is not None and setpoint_ms != self._last_setpoint:
            # Setpoint changed: wipe accumulated statistics so corrections
            # depend only on observations made after the change.
            self._ewma_err = 0.0
            self._ewma_var = 0.0
        self._last_setpoint = setpoint_ms

        instant_error = (latency_ms - setpoint_ms) / setpoint_ms

        innovation = instant_error - self._ewma_err
        new_ewma_err = self._ewma_err + self._ALPHA_ERR * innovation
        new_ewma_var = (1.0 - self._ALPHA_VAR) * self._ewma_var + self._ALPHA_VAR * (innovation * innovation)

        self._ewma_err = new_ewma_err
        self._ewma_var = new_ewma_var

        volatility_factor = 1.0 / (1.0 + self._KV * self._ewma_var)

        if self._ewma_err >= 0.0:
            delta_frac = -self._GAIN_DOWN * self._ewma_err
        else:
            delta_frac = self._GAIN_UP * volatility_factor * (-self._ewma_err)

        unclamped = self.limit * (1.0 + delta_frac)
        new_limit = min(max(unclamped, self.min_limit), self.max_limit)
        self.limit = new_limit

        telemetry = {
            "t": int(obs["t"]),
            "limit": self.limit,
            "ewma_error": self._ewma_err,
            "ewma_variance": self._ewma_var,
            "instant_error": instant_error,
            "volatility_factor": volatility_factor,
            "delta_frac": delta_frac,
            "saturated_high": int(self.limit >= self.max_limit),
            "saturated_low": int(self.limit <= self.min_limit),
            "setpoint_ms": setpoint_ms,
        }

        return {"limit": self.limit, "telemetry": telemetry}

    def snapshot(self) -> str:
        state = {
            "version": self._SCHEMA_VERSION,
            "config": {
                "min_limit": self.min_limit,
                "max_limit": self.max_limit,
                "initial_limit": self.limit,
            },
            "limit": self.limit,
            "ewma_err": self._ewma_err,
            "ewma_var": self._ewma_var,
            "last_setpoint": self._last_setpoint,
        }
        return json.dumps(state, sort_keys=True)

    @classmethod
    def restore(cls, blob: str) -> "Controller":
        state = json.loads(blob)
        ctrl = cls(state["config"])
        ctrl.limit = float(state["limit"])
        ctrl._ewma_err = float(state["ewma_err"])
        ctrl._ewma_var = float(state["ewma_var"])
        ctrl._last_setpoint = (
            None if state["last_setpoint"] is None else float(state["last_setpoint"])
        )
        return ctrl
