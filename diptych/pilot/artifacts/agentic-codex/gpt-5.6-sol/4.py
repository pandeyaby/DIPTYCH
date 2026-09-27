"""Deterministic adaptive concurrency controller.

The implementation is deliberately self contained: its only serialized state is
made up of JSON numbers and booleans, and it has no dependence on process global
state.
"""

from __future__ import annotations

import json
import math


class Controller:
    """A risk-aware, asymmetric PI concurrency controller."""

    # The combined falling response is strictly more than twice the rising
    # response. Severe overload also gets a faster integral escape rate.
    _KI = 3.0
    _KI_OVERLOAD_DOWN = 12.0
    _KP_UP = 4.0
    _KP_DOWN = 14.0
    _ERROR_ALPHA = 0.38
    _VAR_ALPHA = 0.16
    _ARRIVAL_ALPHA = 0.10
    _RISK = 0.42
    _ARRIVAL_RISK = 1.20

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

        initial = min(self.max_limit, max(self.min_limit, self.initial_limit))
        self.limit = initial
        self.integral_limit = initial
        self.setpoint = None
        self.filtered_error = 0.0
        self.error_mean = 0.0
        self.error_variance = 0.0
        self.arrival_mean = 0.0
        self.arrival_variance = 0.0
        self.have_sample = False

    @staticmethod
    def _finite(value: object, name: str) -> float:
        result = float(value)
        if not math.isfinite(result):
            raise ValueError(name + " must be finite")
        return result

    def _reset_observation_state(self) -> None:
        """Forget all measurements belonging to the old setpoint epoch."""
        self.filtered_error = 0.0
        self.error_mean = 0.0
        self.error_variance = 0.0
        self.arrival_mean = 0.0
        self.arrival_variance = 0.0
        self.have_sample = False
        # Start the new epoch at the actual actuator value.  This is also a
        # back-calculation step and prevents a hidden integral residual.
        self.integral_limit = self.limit

    def tick(self, obs: dict) -> dict:
        latency = self._finite(obs["latency_ms"], "latency_ms")
        setpoint = self._finite(obs["setpoint_ms"], "setpoint_ms")
        arrival = self._finite(obs["arrival_rps"], "arrival_rps")
        if setpoint <= 0.0:
            raise ValueError("setpoint_ms must be positive")

        changed = self.setpoint is not None and setpoint != self.setpoint
        if self.setpoint is None or changed:
            self._reset_observation_state()
            self.setpoint = setpoint

        # Positive error permits more concurrency; negative error sheds it.
        error = (setpoint - latency) / setpoint
        sampled_error = min(1.0, max(-1.0, error))

        if not self.have_sample:
            self.filtered_error = sampled_error
            self.error_mean = sampled_error
            self.error_variance = 0.0
            self.arrival_mean = arrival
            self.arrival_variance = 0.0
            self.have_sample = True
        else:
            old_mean = self.error_mean
            self.error_mean = old_mean + self._VAR_ALPHA * (sampled_error - old_mean)
            # This EW variance update is stable and non-negative in exact and
            # floating-point arithmetic (apart from a possible negative zero).
            innovation = sampled_error - old_mean
            self.error_variance = ((1.0 - self._VAR_ALPHA) *
                                   (self.error_variance +
                                    self._VAR_ALPHA * innovation * innovation))
            self.filtered_error += self._ERROR_ALPHA * (sampled_error - self.filtered_error)
            old_arrival_mean = self.arrival_mean
            arrival_innovation = arrival - old_arrival_mean
            self.arrival_mean = (old_arrival_mean + self._ARRIVAL_ALPHA *
                                 arrival_innovation)
            self.arrival_variance = ((1.0 - self._ARRIVAL_ALPHA) *
                                     (self.arrival_variance + self._ARRIVAL_ALPHA *
                                      arrival_innovation * arrival_innovation))

        volatility = math.sqrt(max(0.0, self.error_variance))
        workload_volatility = (math.sqrt(max(0.0, self.arrival_variance)) /
                               max(1.0, abs(self.arrival_mean)))
        adjusted = self.filtered_error - self._RISK * volatility
        # Integrate the current sample, not the smoothed sample.  The integral
        # therefore gives a multiset of observations equal total weight, while
        # the proportional term below is the explicit recency preference.
        integral_signal = sampled_error - self._ARRIVAL_RISK * workload_volatility
        # A latency singularity near full utilization can otherwise turn one
        # observation into a full-range actuator jump.  One unit already means
        # an error equal to the complete setpoint, so larger values contain no
        # useful control information.
        adjusted = min(1.0, max(-1.0, adjusted))
        integral_signal = min(1.0, max(-1.0, integral_signal))

        ki = (self._KI_OVERLOAD_DOWN
              if integral_signal <= -0.5 else self._KI)
        kp = self._KP_UP if adjusted >= 0.0 else self._KP_DOWN

        old_integral = self.integral_limit
        proposed_integral = old_integral + ki * integral_signal
        proposed = proposed_integral + kp * adjusted
        new_limit = min(self.max_limit, max(self.min_limit, proposed))

        saturated_high = new_limit >= self.max_limit
        saturated_low = new_limit <= self.min_limit
        blocked = ((saturated_high and integral_signal > 0.0) or
                   (saturated_low and integral_signal < 0.0))
        if blocked:
            # Conditional integration is the primary anti-windup mechanism.
            # Pinning the favorable filtered state makes escape from a long
            # saturation depend on the new excursion, not saturation duration.
            self.integral_limit = (self.max_limit if saturated_high
                                   else self.min_limit)
            if saturated_high:
                self.filtered_error = 0.0
                self.error_mean = 0.0
                self.error_variance = 0.0
                self.arrival_mean = 0.0
                self.arrival_variance = 0.0
            else:
                self.filtered_error = 0.0
                self.error_mean = 0.0
                self.error_variance = 0.0
                self.arrival_mean = 0.0
                self.arrival_variance = 0.0
            self.have_sample = False
        else:
            self.integral_limit = min(self.max_limit,
                                      max(self.min_limit, proposed_integral))

        previous_limit = self.limit
        self.limit = new_limit
        telemetry = {
            "adjusted_error": adjusted,
            "error": error,
            "filtered_error": self.filtered_error,
            "integral_limit": self.integral_limit,
            "limit_delta": new_limit - previous_limit,
            "setpoint_changed": int(changed),
            "saturation": ("high" if saturated_high else
                           "low" if saturated_low else "none"),
            "volatility": volatility,
            "workload_volatility": workload_volatility,
        }
        return {"limit": float(new_limit), "telemetry": telemetry}

    def snapshot(self) -> str:
        state = {
            "arrival_mean": self.arrival_mean,
            "arrival_variance": self.arrival_variance,
            "error_mean": self.error_mean,
            "error_variance": self.error_variance,
            "filtered_error": self.filtered_error,
            "have_sample": self.have_sample,
            "initial_limit": self.initial_limit,
            "integral_limit": self.integral_limit,
            "limit": self.limit,
            "max_limit": self.max_limit,
            "min_limit": self.min_limit,
            "setpoint": self.setpoint,
            "version": 1,
        }
        return json.dumps(state, sort_keys=True, separators=(",", ":"),
                          allow_nan=False)

    @classmethod
    def restore(cls, blob: str) -> "Controller":
        state = json.loads(blob)
        if not isinstance(state, dict) or state.get("version") != 1:
            raise ValueError("unsupported controller snapshot")
        obj = cls({"min_limit": state["min_limit"],
                   "max_limit": state["max_limit"],
                   "initial_limit": state["initial_limit"]})
        obj.limit = float(state["limit"])
        obj.integral_limit = float(state["integral_limit"])
        obj.setpoint = (None if state["setpoint"] is None
                        else float(state["setpoint"]))
        obj.filtered_error = float(state["filtered_error"])
        obj.error_mean = float(state["error_mean"])
        obj.error_variance = float(state["error_variance"])
        obj.arrival_mean = float(state["arrival_mean"])
        obj.arrival_variance = float(state["arrival_variance"])
        obj.have_sample = bool(state["have_sample"])
        return obj
