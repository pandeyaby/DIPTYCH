"""Deterministic adaptive concurrency controller.

The controller deliberately has no dependencies on wall time or process-global
state.  Its filters are small enough to react to workload changes, while a
variance term leaves headroom when latency is erratic.
"""

from __future__ import annotations

import json
import math


class Controller:
    """Regulate a service's concurrency limit from latency observations."""

    _VERSION = 1
    _ERROR_ALPHA = 0.38
    _TREND_ALPHA = 0.55
    _UP_GAIN = 0.10
    _DOWN_GAIN = 0.34
    _TREND_GAIN = 1.50
    _VOLATILITY_GAIN = 0.15

    def __init__(self, config: dict):
        self.min_limit = float(config["min_limit"])
        self.max_limit = float(config["max_limit"])
        self.limit = float(config["initial_limit"])
        if not (math.isfinite(self.min_limit) and math.isfinite(self.max_limit)):
            raise ValueError("limits must be finite")
        if self.min_limit > self.max_limit:
            raise ValueError("min_limit must not exceed max_limit")
        if not math.isfinite(self.limit):
            raise ValueError("initial_limit must be finite")
        self.limit = self._clamp(self.limit)

        self._setpoint = None
        self._filtered_error = 0.0
        self._error_variance = 0.0
        self._variance_mean = 0.0
        self._variance_samples = 0
        self._trend = 0.0
        self._last_error = 0.0
        self._samples = 0
        # 1 means the preceding command was blocked at max, -1 at min.
        self._blocked = 0

    def _clamp(self, value: float) -> float:
        return min(self.max_limit, max(self.min_limit, value))

    def _clear_filters(self) -> None:
        self._clear_control_filters()
        self._error_variance = 0.0
        self._variance_mean = 0.0
        self._variance_samples = 0

    def _clear_control_filters(self) -> None:
        self._filtered_error = 0.0
        self._trend = 0.0
        self._last_error = 0.0
        self._samples = 0

    def tick(self, obs: dict) -> dict:
        latency = float(obs["latency_ms"])
        setpoint = float(obs["setpoint_ms"])
        if not (math.isfinite(latency) and math.isfinite(setpoint)) or setpoint <= 0.0:
            raise ValueError("latency and setpoint must be finite; setpoint must be positive")

        setpoint_changed = self._setpoint is not None and setpoint != self._setpoint
        if self._setpoint is None or setpoint_changed:
            # A setpoint defines a new control epoch.  No statistic from an old
            # epoch is meaningful in the new error coordinate system.
            self._clear_filters()
            self._blocked = 0
        elif self._blocked:
            # Conditional anti-windup: observations which could only request
            # motion through a hard stop do not remain in the filters.
            self._clear_filters()
        self._setpoint = setpoint

        error = (setpoint - latency) / setpoint
        if self._samples and error * self._filtered_error < 0.0:
            # Do not continue braking after latency has crossed its target (or
            # accelerating after an overshoot).  Besides reducing recovery
            # lag, this bounds the memory of an extreme queueing excursion.
            self._clear_control_filters()

        a = self._ERROR_ALPHA
        if self._variance_samples == 0:
            variance_mean = error
            variance = 0.0
        else:
            variance_innovation = error - self._variance_mean
            variance_mean = self._variance_mean + a * variance_innovation
            variance = ((1.0 - a) *
                        (self._error_variance + a * variance_innovation * variance_innovation))

        if self._samples == 0:
            filtered = error
            trend = 0.0
        else:
            old_filtered = self._filtered_error
            innovation = error - old_filtered
            filtered = old_filtered + a * innovation
            change = error - self._last_error
            trend = self._trend + self._TREND_ALPHA * (change - self._trend)

        volatility = math.sqrt(max(0.0, variance))
        raw_control = (filtered + self._TREND_GAIN * trend
                       - self._VOLATILITY_GAIN * volatility)
        # A single overload sample can be many times the setpoint in a
        # queueing system.  Limiting one-tick authority avoids turning that
        # truthful but transient sample into starvation.
        control = min(1.0, max(-1.0, raw_control))
        gain = self._UP_GAIN if control >= 0.0 else self._DOWN_GAIN
        requested_delta = self.limit * gain * control
        requested = self.limit + requested_delta
        new_limit = self._clamp(requested)
        actual_delta = new_limit - self.limit

        blocked = 0
        if requested > self.max_limit:
            blocked = 1
        elif requested < self.min_limit:
            blocked = -1

        self.limit = new_limit
        self._filtered_error = filtered
        self._error_variance = variance
        self._variance_mean = variance_mean
        self._variance_samples += 1
        self._trend = trend
        self._last_error = error
        self._samples += 1
        self._blocked = blocked

        # Keep this schema literal and unconditional: consumers can depend on
        # every key being present on every tick.
        telemetry = {
            "error": error,
            "filtered_error": filtered,
            "trend": trend,
            "volatility": volatility,
            "control": control,
            "delta": actual_delta,
            "saturation": blocked,
            "samples": self._samples,
            "setpoint": setpoint,
            "setpoint_changed": int(setpoint_changed),
        }
        return {"limit": new_limit, "telemetry": telemetry}

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
                "error_variance": self._error_variance,
                "variance_mean": self._variance_mean,
                "variance_samples": self._variance_samples,
                "trend": self._trend,
                "last_error": self._last_error,
                "samples": self._samples,
                "blocked": self._blocked,
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
        obj._setpoint = (None if state["setpoint"] is None
                         else float(state["setpoint"]))
        obj._filtered_error = float(state["filtered_error"])
        obj._error_variance = float(state["error_variance"])
        obj._variance_mean = float(state["variance_mean"])
        obj._variance_samples = int(state["variance_samples"])
        obj._trend = float(state["trend"])
        obj._last_error = float(state["last_error"])
        obj._samples = int(state["samples"])
        obj._blocked = int(state["blocked"])
        return obj
