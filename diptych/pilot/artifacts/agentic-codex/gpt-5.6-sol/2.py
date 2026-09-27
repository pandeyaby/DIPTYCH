"""Deterministic adaptive concurrency controller.

The controller is deliberately self contained: its decision is a pure function
of the observations seen so far and of the state represented by ``snapshot``.
"""

from __future__ import annotations

import json
import math


class Controller:
    """Regulate a concurrency limit from latency observations."""

    _ERROR_ALPHA = 0.45
    _MEAN_ALPHA = 0.18
    _VAR_ALPHA = 0.18
    _UP_GAIN = 10.0
    _DOWN_GAIN = 26.0
    _TREND_WEIGHT = 1.50
    _VOLATILITY_WEIGHT = 0.55

    def __init__(self, config: dict):
        self.min_limit = float(config.get("min_limit", 1.0))
        self.max_limit = float(config.get("max_limit", 200.0))
        self.limit = float(config.get("initial_limit", 50.0))
        if not all(math.isfinite(x) for x in
                   (self.min_limit, self.max_limit, self.limit)):
            raise ValueError("controller limits must be finite")
        if self.min_limit > self.max_limit:
            raise ValueError("min_limit must not exceed max_limit")
        self.limit = self._clamp(self.limit)

        self.setpoint = None
        self.filtered_error = 0.0
        self.previous_error = None
        self.latency_mean = 0.0
        self.latency_variance = 0.0
        self.sample_count = 0
        self.epoch = 0

    def _clamp(self, value: float) -> float:
        return min(self.max_limit, max(self.min_limit, value))

    def _clear_dynamic_state(self) -> None:
        self.filtered_error = 0.0
        self.previous_error = None
        self.latency_mean = 0.0
        self.latency_variance = 0.0
        self.sample_count = 0

    def tick(self, obs: dict) -> dict:
        latency = float(obs["latency_ms"])
        setpoint = float(obs["setpoint_ms"])
        if not math.isfinite(latency) or latency < 0.0:
            raise ValueError("latency_ms must be a finite non-negative value")
        if not math.isfinite(setpoint) or setpoint <= 0.0:
            raise ValueError("setpoint_ms must be a finite positive value")

        setpoint_changed = self.setpoint is None or setpoint != self.setpoint
        if setpoint_changed:
            self._clear_dynamic_state()
            self.setpoint = setpoint
            self.epoch += 1
        elif self.limit == self.min_limit or self.limit == self.max_limit:
            # Projection anti-windup.  While pinned, do not retain an error or
            # variance reservoir which could be released on leaving the bound.
            self._clear_dynamic_state()

        # A queueing knee can make measured latency effectively unbounded.
        # Limiting the normalized control error prevents one such sample from
        # moving all the way from maximum to minimum concurrency.
        error = max(-1.0, min(1.0, (setpoint - latency) / setpoint))

        if self.sample_count == 0:
            self.filtered_error = error
            self.latency_mean = min(2.0, latency / setpoint)
            self.latency_variance = 0.0
            trend = 0.0
        else:
            self.filtered_error += self._ERROR_ALPHA * (
                error - self.filtered_error)

            # Values far beyond twice the target are overload signals, not a
            # useful estimate of ordinary workload variance.  Winsorising
            # here keeps the variance margin from double-counting an overload
            # already represented by ``error``.
            normalized_latency = min(2.0, latency / setpoint)
            old_mean = self.latency_mean
            self.latency_mean += self._MEAN_ALPHA * (
                normalized_latency - self.latency_mean)
            # An EWMA analogue of Welford's stable variance update.
            innovation_product = ((normalized_latency - old_mean) *
                                  (normalized_latency - self.latency_mean))
            self.latency_variance = ((1.0 - self._VAR_ALPHA) *
                                     self.latency_variance +
                                     self._VAR_ALPHA * innovation_product)
            self.latency_variance = max(0.0, self.latency_variance)

            raw_trend = error - self.previous_error
            # The derivative cannot make a correction larger than the current
            # observed error on its own (important just after a disturbance).
            trend_bound = abs(error)
            trend = min(trend_bound, max(-trend_bound, raw_trend))

        volatility = math.sqrt(self.latency_variance)
        signal = (self.filtered_error + self._TREND_WEIGHT * trend -
                  self._VOLATILITY_WEIGHT * volatility)
        gain = self._UP_GAIN if signal >= 0.0 else self._DOWN_GAIN
        delta = gain * signal
        self.limit = self._clamp(self.limit + delta)

        self.previous_error = error
        self.sample_count += 1

        at_bound = ("max" if self.limit == self.max_limit else
                    "min" if self.limit == self.min_limit else "none")
        telemetry = {
            "delta": delta,
            "epoch": self.epoch,
            "error": error,
            "filtered_error": self.filtered_error,
            "limit": self.limit,
            "sample_count": self.sample_count,
            "saturation": at_bound,
            "setpoint_ms": self.setpoint,
            "signal": signal,
            "trend": trend,
            "volatility": volatility,
        }
        return {"limit": self.limit, "telemetry": telemetry}

    def snapshot(self) -> str:
        state = {
            "config": {
                "initial_limit": self.limit,
                "max_limit": self.max_limit,
                "min_limit": self.min_limit,
            },
            "state": {
                "epoch": self.epoch,
                "filtered_error": self.filtered_error,
                "latency_mean": self.latency_mean,
                "latency_variance": self.latency_variance,
                "limit": self.limit,
                "previous_error": self.previous_error,
                "sample_count": self.sample_count,
                "setpoint": self.setpoint,
            },
            "version": 1,
        }
        return json.dumps(state, sort_keys=True, separators=(",", ":"),
                          allow_nan=False)

    @classmethod
    def restore(cls, blob: str) -> "Controller":
        data = json.loads(blob)
        if data.get("version") != 1:
            raise ValueError("unsupported controller snapshot version")
        controller = cls(data["config"])
        state = data["state"]
        controller.epoch = int(state["epoch"])
        controller.filtered_error = float(state["filtered_error"])
        controller.latency_mean = float(state["latency_mean"])
        controller.latency_variance = float(state["latency_variance"])
        controller.limit = float(state["limit"])
        previous = state["previous_error"]
        controller.previous_error = (None if previous is None else
                                     float(previous))
        controller.sample_count = int(state["sample_count"])
        setpoint = state["setpoint"]
        controller.setpoint = None if setpoint is None else float(setpoint)
        return controller
