"""Deterministic adaptive concurrency controller.

The implementation deliberately has no integral accumulator.  A leaky error
estimate supplies noise rejection, while every change remains a function of
recent, bounded observations.  This also makes clamping at either limit safe:
there is no hidden correction waiting to be unwound later.
"""

from __future__ import annotations

import json
import math


class Controller:
    """Regulate a service's concurrency from latency observations."""

    _VERSION = 1

    # A latency overshoot is intentionally much more expensive than an
    # equally-sized undershoot.  15 / 5 is strictly greater than two.
    _UP_GAIN = 5.0
    _DOWN_GAIN = 15.0
    _ERROR_ALPHA = 0.42
    _VAR_ALPHA = 0.16
    _ARRIVAL_ALPHA = 0.12
    _TREND_GAIN = 0.50
    _VOL_GAIN = 1.25

    def __init__(self, config: dict):
        self.min_limit = float(config["min_limit"])
        self.max_limit = float(config["max_limit"])
        self.initial_limit = float(config["initial_limit"])
        if not (math.isfinite(self.min_limit) and
                math.isfinite(self.max_limit) and
                math.isfinite(self.initial_limit)):
            raise ValueError("controller limits must be finite")
        if self.min_limit > self.max_limit:
            raise ValueError("min_limit must not exceed max_limit")
        if not self.min_limit <= self.initial_limit <= self.max_limit:
            raise ValueError("initial_limit is outside the configured bounds")

        self.limit = self.initial_limit
        self.setpoint = None
        self.error_ema = 0.0
        self.previous_error = 0.0
        self.error_variance = 0.0
        self.arrival_mean = 0.0
        self.arrival_variance = 0.0
        self.epoch_samples = 0
        self.ticks = 0

    @staticmethod
    def _finite(value: object, name: str) -> float:
        result = float(value)
        if not math.isfinite(result):
            raise ValueError(name + " must be finite")
        return result

    def tick(self, obs: dict) -> dict:
        latency = self._finite(obs["latency_ms"], "latency_ms")
        setpoint = self._finite(obs["setpoint_ms"], "setpoint_ms")
        arrival = self._finite(obs["arrival_rps"], "arrival_rps")
        # Read this required field even though feedback uses latency and offered
        # load.  Validation prevents invalid observations silently entering a
        # deterministic run.
        self._finite(obs["admitted_rps"], "admitted_rps")
        if setpoint <= 0.0:
            raise ValueError("setpoint_ms must be positive")

        # Normalization makes tuning independent of the latency unit.  Bounding
        # the error limits a single pathological measurement without storing a
        # backlog of corrective action.
        error = max(-2.0, min(1.0, (setpoint - latency) / setpoint))
        setpoint_changed = self.setpoint is not None and setpoint != self.setpoint

        if self.setpoint is None or setpoint_changed:
            # A setpoint begins a new statistical epoch.  In particular, no
            # pre-change trend or variance can affect a post-change correction.
            self.setpoint = setpoint
            self.error_ema = error
            self.previous_error = error
            self.error_variance = 0.0
            self.arrival_mean = arrival
            self.arrival_variance = 0.0
            self.epoch_samples = 1
            trend = 0.0
        else:
            old_error_mean = self.error_ema
            error_residual = error - old_error_mean
            self.error_ema = old_error_mean + self._ERROR_ALPHA * error_residual
            self.error_variance = ((1.0 - self._VAR_ALPHA) * self.error_variance +
                                   self._VAR_ALPHA * error_residual * error_residual)

            old_arrival_mean = self.arrival_mean
            arrival_residual = arrival - old_arrival_mean
            self.arrival_mean = old_arrival_mean + self._ARRIVAL_ALPHA * arrival_residual
            self.arrival_variance = (
                (1.0 - self._ARRIVAL_ALPHA) * self.arrival_variance +
                self._ARRIVAL_ALPHA * arrival_residual * arrival_residual
            )
            trend = error - self.previous_error
            self.previous_error = error
            self.epoch_samples += 1

        latency_volatility = math.sqrt(max(0.0, self.error_variance))
        arrival_scale = max(1.0, abs(self.arrival_mean))
        arrival_volatility = math.sqrt(max(0.0, self.arrival_variance)) / arrival_scale
        volatility = latency_volatility + 0.35 * arrival_volatility

        # The current sample has the largest coefficient; the leaky estimate
        # and signed trend distinguish improving from worsening orderings.
        signal = (0.68 * error + 0.32 * self.error_ema +
                  self._TREND_GAIN * trend)
        gain = self._UP_GAIN if signal >= 0.0 else self._DOWN_GAIN
        delta = gain * signal - self._VOL_GAIN * volatility
        # At max, the first bad sample cannot release any stored trend or
        # variance as an outsized correction: its observed error alone bounds
        # the downward step.  This is an explicit second line of anti-windup
        # defence in addition to having no integral accumulator.
        if self.limit == self.max_limit and error < 0.0:
            delta = max(delta, -self._DOWN_GAIN * abs(error))
        old_limit = self.limit
        unclamped = old_limit + delta
        self.limit = min(self.max_limit, max(self.min_limit, unclamped))

        if self.limit == self.max_limit:
            clamp_state = "max"
        elif self.limit == self.min_limit:
            clamp_state = "min"
        else:
            clamp_state = "free"

        self.ticks += 1
        telemetry = {
            "arrival_volatility": arrival_volatility,
            "clamp": clamp_state,
            "delta": self.limit - old_limit,
            "epoch_samples": self.epoch_samples,
            "error": error,
            "error_ema": self.error_ema,
            "latency_volatility": latency_volatility,
            "setpoint_epoch": self.setpoint,
            "signal": signal,
            "ticks": self.ticks,
            "trend": trend,
            "volatility": volatility,
        }
        return {"limit": self.limit, "telemetry": telemetry}

    def snapshot(self) -> str:
        state = {
            "arrival_mean": self.arrival_mean,
            "arrival_variance": self.arrival_variance,
            "epoch_samples": self.epoch_samples,
            "error_ema": self.error_ema,
            "error_variance": self.error_variance,
            "initial_limit": self.initial_limit,
            "limit": self.limit,
            "max_limit": self.max_limit,
            "min_limit": self.min_limit,
            "previous_error": self.previous_error,
            "setpoint": self.setpoint,
            "ticks": self.ticks,
            "version": self._VERSION,
        }
        # Sorted, compact JSON is deterministic and Python's float JSON
        # conversion round-trips every finite binary64 value exactly.
        return json.dumps(state, sort_keys=True, separators=(",", ":"), allow_nan=False)

    @classmethod
    def restore(cls, blob: str) -> "Controller":
        state = json.loads(blob)
        if not isinstance(state, dict) or state.get("version") != cls._VERSION:
            raise ValueError("unsupported controller snapshot")
        required = {
            "arrival_mean", "arrival_variance", "epoch_samples", "error_ema",
            "error_variance", "initial_limit", "limit", "max_limit",
            "min_limit", "previous_error", "setpoint", "ticks", "version",
        }
        if set(state) != required:
            raise ValueError("invalid controller snapshot schema")

        obj = cls({
            "min_limit": state["min_limit"],
            "max_limit": state["max_limit"],
            "initial_limit": state["initial_limit"],
        })
        obj.limit = float(state["limit"])
        obj.setpoint = None if state["setpoint"] is None else float(state["setpoint"])
        obj.error_ema = float(state["error_ema"])
        obj.previous_error = float(state["previous_error"])
        obj.error_variance = float(state["error_variance"])
        obj.arrival_mean = float(state["arrival_mean"])
        obj.arrival_variance = float(state["arrival_variance"])
        obj.epoch_samples = int(state["epoch_samples"])
        obj.ticks = int(state["ticks"])
        if not self_or_obj_state_is_valid(obj):
            raise ValueError("invalid values in controller snapshot")
        return obj


def self_or_obj_state_is_valid(obj: Controller) -> bool:
    """Keep restore validation separate from the public interface."""
    floats = (
        obj.min_limit, obj.max_limit, obj.initial_limit, obj.limit,
        obj.error_ema, obj.previous_error, obj.error_variance,
        obj.arrival_mean, obj.arrival_variance,
    )
    return (all(math.isfinite(x) for x in floats) and
            obj.min_limit <= obj.limit <= obj.max_limit and
            obj.error_variance >= 0.0 and obj.arrival_variance >= 0.0 and
            obj.epoch_samples >= 0 and obj.ticks >= 0 and
            (obj.setpoint is None or
             (math.isfinite(obj.setpoint) and obj.setpoint > 0.0)))
