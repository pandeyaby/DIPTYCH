"""Deterministic adaptive concurrency controller.

The controller deliberately has no dependencies on wall time or process-global
state.  Its state is made only from observations, and is fully serializable.
"""

from __future__ import annotations

import json
import math


class Controller:
    """Regulate a service's concurrency limit from latency observations."""

    # An overload is intentionally corrected more strongly than equal spare
    # latency is exploited.  DOWN_GAIN is strictly greater than 2*UP_GAIN.
    _UP_GAIN = 3.6
    _DOWN_GAIN = 9.0
    _EMA_ALPHA = 0.48
    _VAR_ALPHA = 0.12
    _TREND_GAIN = 4.0
    _RISK_GAIN = 0.42

    def __init__(self, config: dict):
        self.min_limit = float(config.get("min_limit", 1.0))
        self.max_limit = float(config.get("max_limit", 200.0))
        self.limit = float(config.get("initial_limit", 50.0))
        if not (math.isfinite(self.min_limit) and
                math.isfinite(self.max_limit) and
                math.isfinite(self.limit)):
            raise ValueError("controller limits must be finite")
        if self.min_limit > self.max_limit:
            raise ValueError("min_limit exceeds max_limit")
        self.limit = min(self.max_limit, max(self.min_limit, self.limit))

        self.setpoint = None
        self.filtered_error = 0.0
        self.error_variance = 0.0
        self.previous_error = 0.0
        self.samples = 0
        self.saturated = "none"

    def _clear_observation_history(self) -> None:
        self.filtered_error = 0.0
        self.error_variance = 0.0
        self.previous_error = 0.0
        self.samples = 0

    def tick(self, obs: dict) -> dict:
        latency = float(obs["latency_ms"])
        setpoint = float(obs["setpoint_ms"])
        if not (math.isfinite(latency) and math.isfinite(setpoint)):
            raise ValueError("latency and setpoint must be finite")
        if setpoint <= 0.0:
            raise ValueError("setpoint_ms must be positive")

        # A regime change is a hard statistical boundary.  In particular, no
        # old EMA, trend, or variance estimate leaks into its first decision.
        setpoint_changed = self.setpoint is not None and setpoint != self.setpoint
        if self.setpoint is None or setpoint_changed:
            self._clear_observation_history()
        self.setpoint = setpoint

        error = (setpoint - latency) / setpoint
        if self.samples == 0:
            filtered = error
            variance = 0.0
            trend = 0.0
        else:
            old_filtered = self.filtered_error
            filtered = old_filtered + self._EMA_ALPHA * (error - old_filtered)
            residual = error - old_filtered
            variance = ((1.0 - self._VAR_ALPHA) * self.error_variance +
                        self._VAR_ALPHA * residual * residual)
            # A positive trend means latency has recently improved.
            trend = error - self.previous_error

        volatility = math.sqrt(max(0.0, variance))
        # Proportional action uses the current sample.  The EMA is used to
        # estimate variance, not repeatedly re-apply an old error (which would
        # paradoxically give early samples more cumulative influence).
        effective_error = error - self._RISK_GAIN * volatility
        gain = self._UP_GAIN if effective_error >= 0.0 else self._DOWN_GAIN
        correction = gain * effective_error + self._TREND_GAIN * trend

        proposed = self.limit + correction
        new_limit = min(self.max_limit, max(self.min_limit, proposed))
        if new_limit >= self.max_limit and proposed >= self.max_limit:
            saturation = "max"
        elif new_limit <= self.min_limit and proposed <= self.min_limit:
            saturation = "min"
        else:
            saturation = "none"

        self.limit = new_limit
        self.filtered_error = filtered
        self.error_variance = variance
        self.previous_error = error
        self.samples += 1
        self.saturated = saturation

        # Anti-windup: while a bound rejects a correction, discard all
        # observation memory.  The first opposite excursion is consequently
        # controlled by that observation alone, not saturation duration.
        # An extreme sample should be acted on immediately, but must not poison
        # several later decisions after the overload itself has disappeared.
        if saturation != "none" or abs(error) > 1.0:
            self._clear_observation_history()

        telemetry = {
            "error": error,
            "filtered_error": self.filtered_error,
            "volatility": math.sqrt(max(0.0, self.error_variance)),
            "trend": trend,
            "correction": correction,
            "saturation": saturation,
            "samples": self.samples,
            "setpoint_changed": int(setpoint_changed),
        }
        return {"limit": float(new_limit), "telemetry": telemetry}

    def snapshot(self) -> str:
        state = {
            "version": 1,
            "min_limit": self.min_limit,
            "max_limit": self.max_limit,
            "limit": self.limit,
            "setpoint": self.setpoint,
            "filtered_error": self.filtered_error,
            "error_variance": self.error_variance,
            "previous_error": self.previous_error,
            "samples": self.samples,
            "saturated": self.saturated,
        }
        return json.dumps(state, sort_keys=True, separators=(",", ":"),
                          allow_nan=False)

    @classmethod
    def restore(cls, blob: str) -> "Controller":
        state = json.loads(blob)
        if state.get("version") != 1:
            raise ValueError("unsupported controller snapshot version")
        controller = cls({
            "min_limit": state["min_limit"],
            "max_limit": state["max_limit"],
            "initial_limit": state["limit"],
        })
        controller.setpoint = state["setpoint"]
        controller.filtered_error = float(state["filtered_error"])
        controller.error_variance = float(state["error_variance"])
        controller.previous_error = float(state["previous_error"])
        controller.samples = int(state["samples"])
        controller.saturated = str(state["saturated"])
        return controller
