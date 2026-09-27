"""Deterministic adaptive concurrency controller.

The controller deliberately keeps a small state vector.  In particular, it
has no accumulating integral term: this makes saturation harmless and makes
the response after a long load-limited period depend on current measurements.
"""

from __future__ import annotations

import json
import math


class Controller:
    """Regulate a service concurrency limit from latency observations."""

    _VERSION = 1
    _ERROR_ALPHA = 0.32
    _MOMENT_ALPHA = 0.12
    _UP_GAIN = 5.0
    _DOWN_GAIN = 12.0
    _VOL_GAIN = 2.0
    _TREND_WEIGHT = 0.5

    def __init__(self, config: dict):
        self.min_limit = float(config.get("min_limit", 1.0))
        self.max_limit = float(config.get("max_limit", 200.0))
        if not (math.isfinite(self.min_limit) and
                math.isfinite(self.max_limit) and
                self.min_limit <= self.max_limit):
            raise ValueError("invalid limit bounds")

        initial = float(config.get("initial_limit", self.min_limit))
        if not math.isfinite(initial):
            raise ValueError("initial_limit must be finite")
        self.limit = self._clamp(initial)

        self.setpoint = None
        self.filtered_error = 0.0
        self.mean_ratio = 0.0
        self.mean_square_ratio = 0.0
        self.samples = 0
        self.previous_error = 0.0

    def _clamp(self, value: float) -> float:
        return min(self.max_limit, max(self.min_limit, value))

    @staticmethod
    def _finite(value: object, name: str) -> float:
        result = float(value)
        if not math.isfinite(result):
            raise ValueError("%s must be finite" % name)
        return result

    def tick(self, obs: dict) -> dict:
        latency = self._finite(obs["latency_ms"], "latency_ms")
        setpoint = self._finite(obs["setpoint_ms"], "setpoint_ms")
        if setpoint <= 0.0:
            raise ValueError("setpoint_ms must be positive")

        # A setpoint defines a separate control epoch.  Reset *all* latency
        # statistics before incorporating the first sample of the new epoch.
        setpoint_changed = self.setpoint is None or setpoint != self.setpoint
        if setpoint_changed:
            self.setpoint = setpoint
            self.filtered_error = 0.0
            self.mean_ratio = 0.0
            self.mean_square_ratio = 0.0
            self.samples = 0
            self.previous_error = 0.0

        ratio = latency / setpoint
        # Clipping only limits the influence of a single pathological sample;
        # it does not create stored windup.
        error = min(1.0, max(-4.0, 1.0 - ratio))

        # Moment clipping prevents a single overload sample from masquerading
        # as lasting workload volatility.
        moment_ratio = min(2.0, max(0.0, ratio))
        leaving_max = (self.limit >= self.max_limit and self.samples > 0
                       and error < 0.0)
        if self.samples == 0 or leaving_max:
            # Discard load-limited history on the first overload sample.  The
            # resulting downward move is exactly bounded by this sample's
            # error, regardless of time spent pinned at max_limit.
            self.filtered_error = error
            self.mean_ratio = moment_ratio
            self.mean_square_ratio = moment_ratio * moment_ratio
            trend = 0.0
            if leaving_max:
                self.samples = 0
        else:
            trend = error - self.previous_error
            ae = self._ERROR_ALPHA
            am = self._MOMENT_ALPHA
            self.filtered_error += ae * (error - self.filtered_error)
            self.mean_ratio += am * (moment_ratio - self.mean_ratio)
            self.mean_square_ratio += am * (
                moment_ratio * moment_ratio - self.mean_square_ratio)
        self.samples += 1
        self.previous_error = error

        variance = max(0.0, self.mean_square_ratio - self.mean_ratio ** 2)
        volatility = math.sqrt(variance)
        # Current error dominates, while the recency-weighted component makes
        # improving and worsening orderings intentionally non-equivalent.
        control_error = (0.75 * error + 0.25 * self.filtered_error
                         + self._TREND_WEIGHT * trend)
        gain = self._UP_GAIN if control_error >= 0.0 else self._DOWN_GAIN
        correction = gain * control_error - self._VOL_GAIN * volatility

        old_limit = self.limit
        self.limit = self._clamp(old_limit + correction)

        # Keep a fixed, flat schema.  Numeric flags avoid JSON boolean quirks
        # in consumers that accept only int/float/str telemetry values.
        telemetry = {
            "correction": correction,
            "error": error,
            "filtered_error": self.filtered_error,
            "limit_before": old_limit,
            "samples": self.samples,
            "setpoint_changed": int(setpoint_changed),
            "volatility": volatility,
        }
        return {"limit": self.limit, "telemetry": telemetry}

    def snapshot(self) -> str:
        state = {
            "filtered_error": self.filtered_error,
            "limit": self.limit,
            "max_limit": self.max_limit,
            "mean_ratio": self.mean_ratio,
            "mean_square_ratio": self.mean_square_ratio,
            "min_limit": self.min_limit,
            "previous_error": self.previous_error,
            "samples": self.samples,
            "setpoint": self.setpoint,
            "version": self._VERSION,
        }
        return json.dumps(state, sort_keys=True, separators=(",", ":"),
                          allow_nan=False)

    @classmethod
    def restore(cls, blob: str) -> "Controller":
        state = json.loads(blob)
        if not isinstance(state, dict) or state.get("version") != cls._VERSION:
            raise ValueError("unsupported controller snapshot")
        obj = cls({
            "min_limit": state["min_limit"],
            "max_limit": state["max_limit"],
            "initial_limit": state["limit"],
        })
        obj.filtered_error = float(state["filtered_error"])
        obj.mean_ratio = float(state["mean_ratio"])
        obj.mean_square_ratio = float(state["mean_square_ratio"])
        obj.previous_error = float(state["previous_error"])
        obj.samples = int(state["samples"])
        value = state["setpoint"]
        obj.setpoint = None if value is None else float(value)
        return obj
