"""Deterministic adaptive concurrency controller.

The controller intentionally uses a leaky (rather than accumulating) error
state.  This gives it useful memory without allowing a command constrained by
one of the limits to wind up.
"""

from __future__ import annotations

import json
import math


class Controller:
    """Regulate a service's concurrency limit from latency observations."""

    _VERSION = 1

    # Constants are part of the algorithm, rather than configurable knobs, so
    # restored controllers cannot accidentally continue under different gains.
    _ERROR_ALPHA = 0.40
    _VAR_ALPHA = 0.12
    _ARRIVAL_ALPHA = 0.12
    _UP_GAIN = 12.0
    _DOWN_GAIN = 31.0
    _TREND_GAIN = 2.0
    _COMMAND_DECAY = 0.97
    _LATENCY_VOL_GAIN = 3.0
    _ARRIVAL_VOL_GAIN = 1.5
    _MAX_UP_STEP_FRACTION = 0.10
    _MAX_DOWN_STEP_FRACTION = 0.25

    def __init__(self, config: dict):
        self.min_limit = float(config.get("min_limit", 1.0))
        self.max_limit = float(config.get("max_limit", 200.0))
        initial = float(config.get("initial_limit", 50.0))
        if not (math.isfinite(self.min_limit) and math.isfinite(self.max_limit)):
            raise ValueError("limits must be finite")
        if self.min_limit > self.max_limit:
            raise ValueError("min_limit must not exceed max_limit")
        if not math.isfinite(initial):
            raise ValueError("initial_limit must be finite")
        self.limit = self._clip(initial, self.min_limit, self.max_limit)
        self.anchor_limit = self.limit
        self.command_offset = 0.0

        self.setpoint = None
        self.error_ema = 0.0
        self.previous_error = 0.0
        self.error_variance = 0.0
        self.arrival_ema = 0.0
        self.arrival_variance = 0.0
        self.samples = 0
        self.last_t = -1

    @staticmethod
    def _clip(value: float, low: float, high: float) -> float:
        return min(max(value, low), high)

    def _reset_observation_state(self, setpoint: float) -> None:
        """Forget all measurements from the preceding setpoint epoch."""
        self.setpoint = setpoint
        self.anchor_limit = self.limit
        self.command_offset = 0.0
        self.error_ema = 0.0
        self.previous_error = 0.0
        self.error_variance = 0.0
        self.arrival_ema = 0.0
        self.arrival_variance = 0.0
        self.samples = 0

    def tick(self, obs: dict) -> dict:
        latency = float(obs["latency_ms"])
        setpoint = float(obs["setpoint_ms"])
        arrival = float(obs["arrival_rps"])
        admitted = float(obs["admitted_rps"])
        tick_index = int(obs["t"])
        if not all(math.isfinite(v) for v in (latency, setpoint, arrival, admitted)):
            raise ValueError("observations must be finite")
        if setpoint <= 0.0:
            raise ValueError("setpoint_ms must be positive")

        setpoint_changed = self.setpoint is None or setpoint != self.setpoint
        if setpoint_changed:
            self._reset_observation_state(setpoint)

        # Positive error asks for more concurrency.  Bounding the normalized
        # error also bounds the response to a single pathological sample.
        error = self._clip((setpoint - latency) / setpoint, -2.0, 1.0)

        if self.samples == 0:
            new_error_ema = error
            error_variance = 0.0
            arrival_ema = arrival
            arrival_variance = 0.0
            trend = 0.0
        else:
            old_error_ema = self.error_ema
            error_innovation = error - old_error_ema
            new_error_ema = old_error_ema + self._ERROR_ALPHA * error_innovation
            error_variance = ((1.0 - self._VAR_ALPHA) * self.error_variance
                              + self._VAR_ALPHA * error_innovation * error_innovation)

            arrival_innovation = arrival - self.arrival_ema
            arrival_ema = self.arrival_ema + self._ARRIVAL_ALPHA * arrival_innovation
            arrival_variance = ((1.0 - self._ARRIVAL_ALPHA) * self.arrival_variance
                                + self._ARRIVAL_ALPHA * arrival_innovation * arrival_innovation)
            trend = new_error_ema - old_error_ema

        # Conditional integration: while pinned at a bound, discard feedback
        # that asks to travel farther through that bound.  Thus the eventual
        # reversal is determined by new samples, not the saturation duration.
        at_max = self.limit >= self.max_limit
        at_min = self.limit <= self.min_limit
        if (at_max and new_error_ema > 0.0) or (at_min and new_error_ema < 0.0):
            new_error_ema = 0.0
            trend = 0.0
            error_variance = 0.0
            # Arrival variance also contributes to the command.  Re-seeding
            # it here prevents time spent at a bound from leaking into the
            # first inward correction.
            arrival_ema = arrival
            arrival_variance = 0.0

        # Act on the current error.  The command offset below is the filtered
        # state; filtering here a second time would make early observations
        # persist longer than recent ones.
        directional_gain = self._UP_GAIN if error >= 0.0 else self._DOWN_GAIN
        feedback = directional_gain * error
        trend_term = self._TREND_GAIN * trend

        latency_volatility = math.sqrt(max(0.0, error_variance))
        arrival_scale = max(abs(arrival_ema), 1.0)
        arrival_volatility = math.sqrt(max(0.0, arrival_variance)) / arrival_scale
        volatility_cost = (self._LATENCY_VOL_GAIN * latency_volatility
                           + self._ARRIVAL_VOL_GAIN * arrival_volatility)

        # Corrections are discounted, making a newer sample intrinsically more
        # influential than an older one.  At a bound the retained offset is
        # not decayed: an inward move then comes only from current feedback,
        # rather than from either accumulated error or decay of a large state.
        bound_holds_offset = ((at_max and new_error_ema <= 0.0)
                              or (at_min and new_error_ema >= 0.0))
        decay = 1.0 if bound_holds_offset else self._COMMAND_DECAY
        proposed_offset = (decay * self.command_offset + feedback
                           + trend_term - volatility_cost)
        desired_limit = self._clip(self.anchor_limit + proposed_offset,
                                   self.min_limit, self.max_limit)
        raw_step = desired_limit - self.limit
        span = self.max_limit - self.min_limit
        max_up_step = self._MAX_UP_STEP_FRACTION * span
        max_down_step = self._MAX_DOWN_STEP_FRACTION * span
        step = self._clip(raw_step, -max_down_step, max_up_step)
        self.limit = self._clip(self.limit + step, self.min_limit, self.max_limit)
        # Store only the realized command so slew limiting cannot itself wind
        # up a hidden target beyond the actual output.
        self.command_offset = self.limit - self.anchor_limit

        self.error_ema = new_error_ema
        self.previous_error = error
        self.error_variance = error_variance
        self.arrival_ema = arrival_ema
        self.arrival_variance = arrival_variance
        self.samples += 1
        self.last_t = tick_index

        # Keep this literal schema stable: keys are never conditional.
        telemetry = {
            "error": error,
            "error_ema": self.error_ema,
            "error_variance": self.error_variance,
            "arrival_ema": self.arrival_ema,
            "arrival_variance": self.arrival_variance,
            "trend": trend,
            "volatility_cost": volatility_cost,
            "feedback": feedback,
            "step": step,
            "setpoint_epoch_samples": self.samples,
            "setpoint_changed": int(setpoint_changed),
            "saturation": "max" if self.limit >= self.max_limit else ("min" if self.limit <= self.min_limit else "none"),
        }
        return {"limit": float(self.limit), "telemetry": telemetry}

    def snapshot(self) -> str:
        state = {
            "version": self._VERSION,
            "min_limit": self.min_limit,
            "max_limit": self.max_limit,
            "limit": self.limit,
            "anchor_limit": self.anchor_limit,
            "command_offset": self.command_offset,
            "setpoint": self.setpoint,
            "error_ema": self.error_ema,
            "previous_error": self.previous_error,
            "error_variance": self.error_variance,
            "arrival_ema": self.arrival_ema,
            "arrival_variance": self.arrival_variance,
            "samples": self.samples,
            "last_t": self.last_t,
        }
        return json.dumps(state, sort_keys=True, separators=(",", ":"), allow_nan=False)

    @classmethod
    def restore(cls, blob: str) -> "Controller":
        state = json.loads(blob)
        if state.get("version") != cls._VERSION:
            raise ValueError("unsupported controller snapshot version")
        obj = cls({
            "min_limit": state["min_limit"],
            "max_limit": state["max_limit"],
            "initial_limit": state["limit"],
        })
        obj.setpoint = state["setpoint"]
        obj.anchor_limit = float(state["anchor_limit"])
        obj.command_offset = float(state["command_offset"])
        obj.error_ema = float(state["error_ema"])
        obj.previous_error = float(state["previous_error"])
        obj.error_variance = float(state["error_variance"])
        obj.arrival_ema = float(state["arrival_ema"])
        obj.arrival_variance = float(state["arrival_variance"])
        obj.samples = int(state["samples"])
        obj.last_t = int(state["last_t"])
        return obj
