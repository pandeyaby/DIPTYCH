"""Deterministic adaptive concurrency controller.

The controller is deliberately a bounded, incremental controller rather than
an integral controller.  That makes saturation harmless: there is no hidden
integral term which can wind up while an actuator is pinned at a bound.
"""

from __future__ import annotations

import json
import math


class Controller:
    """Regulate a service concurrency limit from latency observations."""

    _VERSION = 1

    # Controller constants are part of the implementation (and, consequently,
    # of the snapshot version).  DOWN_GAIN is strictly greater than twice
    # UP_GAIN.
    _UP_GAIN = 8.0
    _DOWN_GAIN = 22.0
    _ERROR_ALPHA = 0.35
    _VAR_ALPHA = 0.12
    _TREND_GAIN = 2.0
    _VOL_GAIN = 4.0
    _DEADBAND = 0.006

    _TELEMETRY_KEYS = (
        "error",
        "filtered_error",
        "trend",
        "volatility",
        "raw_delta",
        "applied_delta",
        "bound",
        "epoch",
    )

    def __init__(self, config: dict):
        self.min_limit = float(config.get("min_limit", 1.0))
        self.max_limit = float(config.get("max_limit", 200.0))
        self.limit = float(config.get("initial_limit", 50.0))

        if not (math.isfinite(self.min_limit) and math.isfinite(self.max_limit)):
            raise ValueError("limit bounds must be finite")
        if self.min_limit > self.max_limit:
            raise ValueError("min_limit must not exceed max_limit")
        if not math.isfinite(self.limit):
            raise ValueError("initial_limit must be finite")
        self.limit = self._clamp(self.limit)

        self._setpoint = None
        self._filtered_error = 0.0
        self._error_mean = 0.0
        self._error_var = 0.0
        self._have_sample = False
        self._epoch = 0

    def _clamp(self, value: float) -> float:
        return min(self.max_limit, max(self.min_limit, value))

    def _clear_history(self) -> None:
        self._filtered_error = 0.0
        self._error_mean = 0.0
        self._error_var = 0.0
        self._have_sample = False

    def tick(self, obs: dict) -> dict:
        latency = float(obs["latency_ms"])
        setpoint = float(obs["setpoint_ms"])
        if not math.isfinite(latency) or not math.isfinite(setpoint) or setpoint <= 0.0:
            raise ValueError("latency and a positive setpoint must be finite")

        # A setpoint starts a new control epoch.  No filter, trend, or variance
        # estimate from the old operating regime is allowed into this tick.
        setpoint_changed = self._setpoint is not None and setpoint != self._setpoint
        if self._setpoint is None or setpoint_changed:
            self._clear_history()
            if setpoint_changed:
                self._epoch += 1
        self._setpoint = setpoint

        # Positive error means latency is below target and capacity may grow.
        error = (setpoint - latency) / setpoint

        if self._have_sample:
            old_filtered = self._filtered_error
            self._filtered_error += self._ERROR_ALPHA * (error - self._filtered_error)
            trend = self._filtered_error - old_filtered

            # Exponentially weighted variance.  This stable one-pass update
            # measures variation around the local mean rather than mistaking a
            # sustained offset for volatility.
            old_mean = self._error_mean
            self._error_mean += self._VAR_ALPHA * (error - self._error_mean)
            self._error_var = (1.0 - self._VAR_ALPHA) * (
                self._error_var + self._VAR_ALPHA * (error - old_mean) ** 2
            )
        else:
            self._filtered_error = error
            self._error_mean = error
            self._error_var = 0.0
            trend = 0.0
            self._have_sample = True

        volatility = math.sqrt(max(0.0, self._error_var))
        signal = self._filtered_error + self._TREND_GAIN * trend
        if abs(signal) < self._DEADBAND:
            signal = 0.0

        gain = self._UP_GAIN if signal >= 0.0 else self._DOWN_GAIN
        raw_delta = gain * signal - self._VOL_GAIN * volatility
        old_limit = self.limit
        proposed_limit = old_limit + raw_delta

        # In the severe-overload region the latency curve is nearly vertical,
        # so latency magnitude is a poor linear actuator signal.  The admitted
        # concurrency is a much better scale: retreat below the concurrency
        # which produced the observation, without collapsing to the minimum.
        severe_overload = latency > 2.0 * setpoint
        if severe_overload:
            admitted = float(obs["admitted_rps"])
            if math.isfinite(admitted) and admitted > 0.0:
                proposed_limit = max(proposed_limit, 0.82 * admitted)

        self.limit = self._clamp(proposed_limit)
        applied_delta = self.limit - old_limit

        if self.limit >= self.max_limit:
            bound = "max"
        elif self.limit <= self.min_limit:
            bound = "min"
        else:
            bound = "none"

        telemetry = {
            "error": error,
            "filtered_error": self._filtered_error,
            "trend": trend,
            "volatility": volatility,
            "raw_delta": raw_delta,
            "applied_delta": applied_delta,
            "bound": bound,
            "epoch": self._epoch,
        }

        # Once an actuator is saturated, retaining a velocity history cannot
        # improve the current decision and can only create windup.  The next
        # tick therefore responds afresh to its immediately observed error.
        if bound != "none" or severe_overload:
            self._clear_history()

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
                "setpoint": self._setpoint,
                "filtered_error": self._filtered_error,
                "error_mean": self._error_mean,
                "error_var": self._error_var,
                "have_sample": self._have_sample,
                "epoch": self._epoch,
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
        obj._setpoint = None if state["setpoint"] is None else float(state["setpoint"])
        obj._filtered_error = float(state["filtered_error"])
        obj._error_mean = float(state["error_mean"])
        obj._error_var = float(state["error_var"])
        obj._have_sample = bool(state["have_sample"])
        obj._epoch = int(state["epoch"])
        return obj
