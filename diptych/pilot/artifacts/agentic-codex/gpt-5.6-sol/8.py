"""Deterministic adaptive concurrency controller.

The controller deliberately has no unbounded integral term.  Its latency and
variance estimators are leaky, and growth is cautious while demand is below
the concurrency limit.
"""

from __future__ import annotations

import json
import math


class Controller:
    """Regulate an admitted-concurrency limit from latency observations."""

    _VERSION = 1

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
        self.setpoint = None
        self.filtered_error = 0.0
        self.error_variance = 0.0
        self.previous_error = 0.0
        self.samples = 0
        self.last_t = -1

    def _clamp(self, value: float) -> float:
        return min(self.max_limit, max(self.min_limit, value))

    @staticmethod
    def _number(obs: dict, key: str) -> float:
        value = float(obs[key])
        if not math.isfinite(value):
            raise ValueError("observation %s must be finite" % key)
        return value

    def tick(self, obs: dict) -> dict:
        latency = self._number(obs, "latency_ms")
        setpoint = self._number(obs, "setpoint_ms")
        arrival = self._number(obs, "arrival_rps")
        admitted = self._number(obs, "admitted_rps")
        tick_index = int(obs["t"])
        if latency <= 0.0 or setpoint <= 0.0:
            raise ValueError("latency and setpoint must be positive")

        changed = self.setpoint is None or setpoint != self.setpoint
        if changed:
            # A setpoint defines a new control epoch.  No statistic from the
            # old epoch is allowed to affect a correction in the new one.
            self.setpoint = setpoint
            self.filtered_error = 0.0
            self.error_variance = 0.0
            self.previous_error = 0.0
            self.samples = 0

        # Positive error means latency headroom and therefore permits growth.
        error = (setpoint - latency) / setpoint
        error = min(0.9, max(-3.0, error))

        if self.samples == 0:
            filtered = error
            variance = 0.0
            trend = 0.0
        else:
            alpha = 0.42
            old_filtered = self.filtered_error
            filtered = old_filtered + alpha * (error - old_filtered)
            innovation = error - old_filtered
            variance = (0.86 * self.error_variance +
                        0.14 * innovation * innovation)
            # Raw first differences give recency an explicit, deterministic
            # influence: the same samples ending well produce a higher limit.
            trend = error - self.previous_error

        sigma = math.sqrt(max(0.0, variance))

        # Latency feedback is intentionally asymmetric.  The downward response
        # to a one-sample excursion is bounded by DOWN_GAIN * observed error.
        up_gain = 2.5
        down_gain = 10.0
        # Trend is part of the error signal (rather than a separately added
        # action), so the asymmetric gain applies to the complete correction.
        # This preserves the response ratio for both static and trending
        # deviations while making the ordering of observations matter.
        volatility_term = 0.18 * sigma if self.samples else 0.0
        signal = filtered + 1.50 * trend - volatility_term
        delta = (up_gain if signal >= 0.0 else down_gain) * signal

        if error < 0.0:
            # Anti-windup/excursion bound.  State accumulated while clamped at
            # max can never amplify the first downward action.
            delta = max(delta, down_gain * error)

        # If arrivals did not reach the old limit, latency cannot identify
        # additional capacity.  Permit only cautious growth in that case.
        demand_limited = arrival < self.limit * 0.98 or admitted < self.limit * 0.98
        if demand_limited and delta > 0.0:
            delta *= 0.18

        new_limit = self._clamp(self.limit + delta)
        applied = new_limit - self.limit
        self.limit = new_limit
        self.filtered_error = filtered
        self.error_variance = variance
        self.previous_error = error
        self.samples += 1
        self.last_t = tick_index

        # Keep this literal schema stable across all branches and epochs.
        telemetry = {
            "applied_delta": float(applied),
            "demand_limited": int(demand_limited),
            "error": float(error),
            "filtered_error": float(filtered),
            "setpoint_epoch_samples": int(self.samples),
            "setpoint_reset": int(changed),
            "trend": float(trend),
            "volatility": float(sigma),
        }
        return {"limit": float(new_limit), "telemetry": telemetry}

    def snapshot(self) -> str:
        state = {
            "config": {
                "initial_limit": self.initial_limit,
                "max_limit": self.max_limit,
                "min_limit": self.min_limit,
            },
            "state": {
                "error_variance": self.error_variance,
                "filtered_error": self.filtered_error,
                "last_t": self.last_t,
                "limit": self.limit,
                "previous_error": self.previous_error,
                "samples": self.samples,
                "setpoint": self.setpoint,
            },
            "version": self._VERSION,
        }
        return json.dumps(state, sort_keys=True, separators=(",", ":"),
                          allow_nan=False)

    @classmethod
    def restore(cls, blob: str) -> "Controller":
        data = json.loads(blob)
        if data.get("version") != cls._VERSION:
            raise ValueError("unsupported controller snapshot version")
        obj = cls(data["config"])
        state = data["state"]
        obj.limit = float(state["limit"])
        obj.setpoint = (None if state["setpoint"] is None
                        else float(state["setpoint"]))
        obj.filtered_error = float(state["filtered_error"])
        obj.error_variance = float(state["error_variance"])
        obj.previous_error = float(state["previous_error"])
        obj.samples = int(state["samples"])
        obj.last_t = int(state["last_t"])
        if not (obj.min_limit <= obj.limit <= obj.max_limit):
            raise ValueError("snapshot limit is outside configured bounds")
        return obj
