"""Deterministic adaptive concurrency controller.

The controller deliberately has no accumulating integral term.  That makes
the actuator bounds genuine anti-windup bounds rather than places at which a
hidden control signal can continue to grow.
"""

from __future__ import annotations

import json
import math


class Controller:
    """Regulate a service concurrency limit from latency observations."""

    _VERSION = 1
    _ALPHA = 0.45
    _VAR_ALPHA = 0.16
    _TREND_WEIGHT = 1.00
    _VOL_WEIGHT = 0.40
    _UP_GAIN = 5.0
    _DOWN_GAIN = 16.0

    def __init__(self, config: dict):
        self.min_limit = float(config.get("min_limit", 1.0))
        self.max_limit = float(config.get("max_limit", 200.0))
        self.limit = float(config.get("initial_limit", 50.0))
        if not (
            math.isfinite(self.min_limit)
            and math.isfinite(self.max_limit)
            and math.isfinite(self.limit)
            and self.min_limit <= self.max_limit
        ):
            raise ValueError("invalid controller configuration")
        self.limit = self._clip(self.limit)

        self.setpoint = None
        self.error_ema = 0.0
        self.error_var = 0.0
        self.previous_error = 0.0
        self.sample_count = 0
        self.last_t = -1

    def _clip(self, value: float) -> float:
        return min(self.max_limit, max(self.min_limit, value))

    def tick(self, obs: dict) -> dict:
        latency = float(obs["latency_ms"])
        setpoint = float(obs["setpoint_ms"])
        tick_index = int(obs["t"])
        if not (math.isfinite(latency) and latency >= 0.0):
            raise ValueError("latency_ms must be finite and non-negative")
        if not (math.isfinite(setpoint) and setpoint > 0.0):
            raise ValueError("setpoint_ms must be finite and positive")

        # A relative error gives identical tuning at different setpoints.
        raw_error = (setpoint - latency) / setpoint
        # A single overload sample can be many setpoints high because queueing
        # latency is nonlinear.  Bounding the normalized error prevents that
        # one sample from starving the service for dozens of later ticks.
        error = min(1.0, max(-1.0, raw_error))
        changed = self.setpoint is not None and setpoint != self.setpoint
        saturated_reversal = self.limit == self.max_limit and error < 0.0

        if self.setpoint is None or changed or saturated_reversal:
            # The first sample belongs wholly to the new control epoch.  No
            # statistic from the old setpoint is allowed across this boundary.
            # The same reset on a reversal at the upper actuator bound is the
            # anti-windup rule: time spent requesting still more concurrency
            # cannot magnify the first downward correction.
            self.error_ema = error
            self.error_var = 0.0
            self.previous_error = error
            self.sample_count = 1
            trend = 0.0
        else:
            old_mean = self.error_ema
            self.error_ema = old_mean + self._ALPHA * (error - old_mean)
            # EW variance of the error.  The product form is the stable online
            # update corresponding to the newly updated mean.
            innovation = error - old_mean
            self.error_var = (
                (1.0 - self._VAR_ALPHA) * self.error_var
                + self._VAR_ALPHA * innovation * (error - self.error_ema)
            )
            if self.error_var < 0.0:  # protect against round-off only
                self.error_var = 0.0
            trend = error - self.previous_error
            self.previous_error = error
            self.sample_count += 1

        volatility = math.sqrt(self.error_var)
        signal = (
            self.error_ema
            + self._TREND_WEIGHT * trend
            - self._VOL_WEIGHT * volatility
        )
        gain = self._UP_GAIN if signal >= 0.0 else self._DOWN_GAIN
        requested_delta = gain * signal
        old_limit = self.limit
        self.limit = self._clip(old_limit + requested_delta)
        applied_delta = self.limit - old_limit

        self.setpoint = setpoint
        self.last_t = tick_index
        telemetry = {
            "error": error,
            "error_ema": self.error_ema,
            "error_variance": self.error_var,
            "volatility": volatility,
            "trend": trend,
            "control_signal": signal,
            "requested_delta": requested_delta,
            "applied_delta": applied_delta,
            "sample_count": self.sample_count,
            "setpoint_epoch": setpoint,
            "status": (
                "max" if self.limit == self.max_limit
                else "min" if self.limit == self.min_limit
                else "active"
            ),
        }
        return {"limit": self.limit, "telemetry": telemetry}

    def snapshot(self) -> str:
        state = {
            "version": self._VERSION,
            "config": {
                "min_limit": self.min_limit,
                "max_limit": self.max_limit,
                "initial_limit": self.limit,
            },
            "state": {
                "limit": self.limit,
                "setpoint": self.setpoint,
                "error_ema": self.error_ema,
                "error_var": self.error_var,
                "previous_error": self.previous_error,
                "sample_count": self.sample_count,
                "last_t": self.last_t,
            },
        }
        return json.dumps(state, sort_keys=True, separators=(",", ":"), allow_nan=False)

    @classmethod
    def restore(cls, blob: str) -> "Controller":
        data = json.loads(blob)
        if data.get("version") != cls._VERSION:
            raise ValueError("unsupported controller snapshot version")
        obj = cls(data["config"])
        state = data["state"]
        obj.limit = float(state["limit"])
        raw_setpoint = state["setpoint"]
        obj.setpoint = None if raw_setpoint is None else float(raw_setpoint)
        obj.error_ema = float(state["error_ema"])
        obj.error_var = float(state["error_var"])
        obj.previous_error = float(state["previous_error"])
        obj.sample_count = int(state["sample_count"])
        obj.last_t = int(state["last_t"])
        if obj.limit != obj._clip(obj.limit):
            raise ValueError("snapshot limit outside configured bounds")
        return obj
