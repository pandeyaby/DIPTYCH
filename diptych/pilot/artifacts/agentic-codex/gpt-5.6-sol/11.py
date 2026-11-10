"""Deterministic adaptive concurrency controller.

The controller deliberately has no integral term.  In this application an
integrator mostly remembers periods in which the actuator could not affect
latency (offered load was below the limit), and consequently winds up.
"""

from __future__ import annotations

import json
import math


class Controller:
    """A bounded, asymmetric EWMA feedback controller."""

    _VERSION = 1
    _ALPHA = 0.45
    _VAR_ALPHA = 0.16
    _UP_GAIN = 0.040
    _DOWN_GAIN = 0.125
    _TREND_GAIN = 0.15
    _VOL_GAIN = 0.012
    _MAX_UP_FRACTION = 0.045
    _MAX_DOWN_FRACTION = 0.18

    def __init__(self, config: dict):
        self.min_limit = float(config.get("min_limit", 1.0))
        self.max_limit = float(config.get("max_limit", 200.0))
        initial = float(config.get("initial_limit", 50.0))
        if not (math.isfinite(self.min_limit) and
                math.isfinite(self.max_limit) and
                self.min_limit <= self.max_limit):
            raise ValueError("invalid limit bounds")
        if not math.isfinite(initial):
            raise ValueError("initial_limit must be finite")

        self.limit = self._clamp(initial)
        self.setpoint = None
        self.filtered_error = 0.0
        self.error_variance = 0.0
        self.previous_error = 0.0
        self.samples = 0
        self.last_t = -1

    def _clamp(self, value: float) -> float:
        return min(self.max_limit, max(self.min_limit, value))

    @staticmethod
    def _finite_number(value: object, name: str) -> float:
        try:
            result = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(name + " must be a finite number") from exc
        if not math.isfinite(result):
            raise ValueError(name + " must be a finite number")
        return result

    def tick(self, obs: dict) -> dict:
        latency = self._finite_number(obs["latency_ms"], "latency_ms")
        setpoint = self._finite_number(obs["setpoint_ms"], "setpoint_ms")
        arrival = self._finite_number(obs["arrival_rps"], "arrival_rps")
        admitted = self._finite_number(obs["admitted_rps"], "admitted_rps")
        tick_index = int(obs["t"])
        if setpoint <= 0.0:
            raise ValueError("setpoint_ms must be positive")

        changed = self.setpoint is None or setpoint != self.setpoint
        if changed:
            # This is a regime boundary: no estimator state crosses it.
            self.setpoint = setpoint
            self.filtered_error = 0.0
            self.error_variance = 0.0
            self.previous_error = 0.0
            self.samples = 0

        # Positive error asks for more concurrency.  Capping the normalized
        # error makes a single pathological latency sample recoverable while
        # retaining its direction and relative size in the normal range.
        error = (setpoint - latency) / setpoint
        error = min(1.0, max(-2.0, error))

        saturated = (self.limit <= self.min_limit or
                     self.limit >= self.max_limit)
        if saturated:
            # Do not let an estimator accumulate state while the actuator is
            # pinned.  In particular, an excursion from max_limit is handled
            # solely according to the newly observed error.
            self.filtered_error = 0.0
            self.error_variance = 0.0
            self.previous_error = 0.0
            self.samples = 0

        if self.samples == 0:
            filtered = error
            variance = 0.0
            trend = 0.0
        else:
            old_mean = self.filtered_error
            filtered = old_mean + self._ALPHA * (error - old_mean)
            innovation = error - old_mean
            variance = ((1.0 - self._VAR_ALPHA) * self.error_variance +
                        self._VAR_ALPHA * innovation * innovation)
            trend = filtered - old_mean

        span = self.max_limit - self.min_limit
        if filtered >= 0.0:
            feedback = self._UP_GAIN * span * filtered
            mode = "increase"
        else:
            feedback = self._DOWN_GAIN * span * filtered
            mode = "decrease"

        # Variance is a one-sided risk cost.  It is deliberately omitted for
        # the first sample in a regime, where variance is not yet observable.
        volatility = math.sqrt(max(0.0, variance))
        risk_cost = self._VOL_GAIN * span * volatility
        # A positive trend means latency is improving.  The difference term
        # gives genuinely recent samples more influence than the same samples
        # presented in the opposite order.
        trend_adjustment = self._TREND_GAIN * span * trend
        raw_delta = feedback + trend_adjustment - risk_cost
        delta = min(self._MAX_UP_FRACTION * span,
                    max(-self._MAX_DOWN_FRACTION * span, raw_delta))
        old_limit = self.limit
        self.limit = self._clamp(old_limit + delta)
        applied_delta = self.limit - old_limit

        self.filtered_error = filtered
        self.error_variance = variance
        self.previous_error = error
        self.samples += 1
        self.last_t = tick_index

        telemetry = {
            "error": error,
            "filtered_error": self.filtered_error,
            "error_variance": self.error_variance,
            "volatility": volatility,
            "risk_cost": risk_cost,
            "feedback": feedback,
            "trend": trend,
            "trend_adjustment": trend_adjustment,
            "requested_delta": delta,
            "applied_delta": applied_delta,
            "arrival_rps": arrival,
            "admitted_rps": admitted,
            "samples_in_regime": self.samples,
            "setpoint_changed": int(changed),
            "actuator_saturated": int(saturated),
            "mode": mode,
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
                "filtered_error": self.filtered_error,
                "error_variance": self.error_variance,
                "previous_error": self.previous_error,
                "samples": self.samples,
                "last_t": self.last_t,
            },
        }
        return json.dumps(state, sort_keys=True, separators=(",", ":"),
                          allow_nan=False)

    @classmethod
    def restore(cls, blob: str) -> "Controller":
        try:
            saved = json.loads(blob)
            if saved["version"] != cls._VERSION:
                raise ValueError("unsupported snapshot version")
            obj = cls(saved["config"])
            state = saved["state"]
            obj.limit = obj._clamp(obj._finite_number(state["limit"], "limit"))
            setpoint = state["setpoint"]
            obj.setpoint = (None if setpoint is None else
                            obj._finite_number(setpoint, "setpoint"))
            obj.filtered_error = obj._finite_number(
                state["filtered_error"], "filtered_error")
            obj.error_variance = obj._finite_number(
                state["error_variance"], "error_variance")
            obj.previous_error = obj._finite_number(
                state["previous_error"], "previous_error")
            obj.samples = int(state["samples"])
            obj.last_t = int(state["last_t"])
            if obj.samples < 0 or obj.error_variance < 0.0:
                raise ValueError("invalid snapshot state")
            return obj
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError("invalid controller snapshot") from exc
