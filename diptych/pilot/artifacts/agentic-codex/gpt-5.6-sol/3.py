"""Deterministic adaptive concurrency controller.

The controller deliberately has no integral term.  A recency-weighted error
provides useful smoothing and trend sensitivity, while conditional resets at
the bounds give it the anti-windup behaviour of a clamped PI controller
without accumulating a hidden, unbounded state.
"""

from __future__ import annotations

import json
import math


class Controller:
    """Regulate a service concurrency limit from latency observations."""

    _VERSION = 1
    _ERROR_ALPHA = 0.40
    _VAR_ALPHA = 0.16
    _UP_GAIN = 0.070
    _DOWN_GAIN = 0.220
    _VOL_GAIN = 0.040
    _TREND_GAIN = 0.080
    _MAX_STEP_FRACTION = 0.30

    def __init__(self, config: dict):
        self.min_limit = float(config.get("min_limit", 1.0))
        self.max_limit = float(config.get("max_limit", 200.0))
        self.initial_limit = float(config.get("initial_limit", 50.0))
        if not (math.isfinite(self.min_limit) and
                math.isfinite(self.max_limit) and
                math.isfinite(self.initial_limit)):
            raise ValueError("controller limits must be finite")
        if self.min_limit > self.max_limit:
            raise ValueError("min_limit must not exceed max_limit")
        self.limit = self._clamp(self.initial_limit)
        self._setpoint = None
        self._mean_error = 0.0
        self._variance = 0.0
        self._last_error = None
        self._samples = 0
        self._last_t = -1

    def _clamp(self, value: float) -> float:
        return min(self.max_limit, max(self.min_limit, value))

    def _clear_filter(self) -> None:
        self._mean_error = 0.0
        self._variance = 0.0
        self._last_error = None
        self._samples = 0

    def tick(self, obs: dict) -> dict:
        latency = float(obs["latency_ms"])
        setpoint = float(obs["setpoint_ms"])
        tick_index = int(obs["t"])
        if not math.isfinite(latency) or not math.isfinite(setpoint):
            raise ValueError("latency and setpoint must be finite")
        if setpoint <= 0.0:
            raise ValueError("setpoint_ms must be positive")

        setpoint_changed = self._setpoint is None or setpoint != self._setpoint
        if setpoint_changed:
            # Measurements normalized to another target must not influence the
            # response to this target.
            self._clear_filter()
            self._setpoint = setpoint

        error = (latency - setpoint) / setpoint
        # A single overload sample can be many times the target in a queueing
        # system.  It should trigger a firm correction, but must not poison the
        # smoothing and variance estimates for dozens of later ticks.
        bounded_error = max(-1.0, min(1.0, error))
        trend = (0.0 if self._last_error is None
                 else bounded_error - self._last_error)
        if self._samples == 0:
            self._mean_error = bounded_error
            self._variance = 0.0
            self._samples = 1
        else:
            old_mean = self._mean_error
            self._mean_error = old_mean + self._ERROR_ALPHA * (bounded_error - old_mean)
            # An exponentially weighted analogue of Welford's update.  It is
            # deterministic, non-negative, and responds to changing variance.
            innovation = bounded_error - old_mean
            self._variance = ((1.0 - self._VAR_ALPHA) * self._variance +
                              self._VAR_ALPHA * innovation * innovation)
            self._samples += 1

        volatility = math.sqrt(max(0.0, self._variance))
        signal = 0.55 * bounded_error + 0.45 * self._mean_error
        gain = self._DOWN_GAIN if signal >= 0.0 else self._UP_GAIN
        control_fraction = (-gain * signal - self._VOL_GAIN * volatility -
                            self._TREND_GAIN * trend)
        control_fraction = max(-self._MAX_STEP_FRACTION,
                               min(self._MAX_STEP_FRACTION, control_fraction))

        old_limit = self.limit
        requested = old_limit * (1.0 + control_fraction)
        self.limit = self._clamp(requested)
        adjustment = self.limit - old_limit
        at_max = self.limit >= self.max_limit
        at_min = self.limit <= self.min_limit

        # Conditional integration: observations asking to move farther past a
        # bound are discarded.  Thus a later excursion starts from that new
        # observation rather than from the duration of saturation.
        outward_saturation = ((at_max and control_fraction >= 0.0) or
                              (at_min and control_fraction <= 0.0))
        self._last_error = bounded_error
        if outward_saturation:
            self._clear_filter()

        self._last_t = tick_index
        telemetry = {
            "adjustment": float(adjustment),
            "error": float(error),
            "filtered_error": float(self._mean_error),
            "limit": float(self.limit),
            "samples": int(self._samples),
            "saturation": "max" if at_max else ("min" if at_min else "none"),
            "setpoint_changed": int(setpoint_changed),
            "trend": float(trend),
            "volatility": float(math.sqrt(max(0.0, self._variance))),
        }
        return {"limit": float(self.limit), "telemetry": telemetry}

    def snapshot(self) -> str:
        state = {
            "initial_limit": self.initial_limit,
            "last_error": self._last_error,
            "last_t": self._last_t,
            "limit": self.limit,
            "max_limit": self.max_limit,
            "mean_error": self._mean_error,
            "min_limit": self.min_limit,
            "samples": self._samples,
            "setpoint": self._setpoint,
            "variance": self._variance,
            "version": self._VERSION,
        }
        return json.dumps(state, sort_keys=True, separators=(",", ":"),
                          allow_nan=False)

    @classmethod
    def restore(cls, blob: str) -> "Controller":
        state = json.loads(blob)
        if state.get("version") != cls._VERSION:
            raise ValueError("unsupported controller snapshot version")
        obj = cls({
            "min_limit": state["min_limit"],
            "max_limit": state["max_limit"],
            "initial_limit": state["initial_limit"],
        })
        obj.limit = float(state["limit"])
        obj._setpoint = (None if state["setpoint"] is None
                         else float(state["setpoint"]))
        obj._mean_error = float(state["mean_error"])
        obj._variance = float(state["variance"])
        obj._last_error = (None if state["last_error"] is None
                           else float(state["last_error"]))
        obj._samples = int(state["samples"])
        obj._last_t = int(state["last_t"])
        return obj
