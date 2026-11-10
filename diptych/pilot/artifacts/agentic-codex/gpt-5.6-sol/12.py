"""Deterministic adaptive concurrency controller.

The controller is deliberately self contained: it has no clock or process-global
state, and every value which affects a future decision is included in snapshots.
"""

from __future__ import annotations

import json
import math


class Controller:
    """A recency-weighted, asymmetric latency controller."""

    _VERSION = 1
    _ALPHA = 0.35
    _UP_GAIN = 10.0
    _DOWN_GAIN = 30.0
    _TREND_GAIN = 0.70
    _RISK_GAIN = 18.0

    def __init__(self, config: dict):
        self.min_limit = float(config.get("min_limit", 1.0))
        self.max_limit = float(config.get("max_limit", 200.0))
        if not (math.isfinite(self.min_limit) and
                math.isfinite(self.max_limit) and
                self.min_limit <= self.max_limit):
            raise ValueError("invalid concurrency limit bounds")

        initial = float(config.get("initial_limit", 50.0))
        if not math.isfinite(initial):
            raise ValueError("initial_limit must be finite")
        self.limit = self._clip(initial)

        self.setpoint = None
        self.filtered_error = 0.0
        self.error_mean = 0.0
        self.error_var = 0.0
        self.risk_margin = 0.0
        self.samples = 0
        self.epoch = 0

    def _clip(self, value: float) -> float:
        return min(self.max_limit, max(self.min_limit, value))

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
        admitted = self._finite(obs["admitted_rps"], "admitted_rps")
        if setpoint <= 0.0:
            raise ValueError("setpoint_ms must be positive")

        # A changed target starts a new statistical epoch.  In particular, no
        # old trend, variance, or risk-margin release affects this decision.
        changed = self.setpoint is None or setpoint != self.setpoint
        if changed:
            if self.setpoint is not None:
                self.epoch += 1
            self.setpoint = setpoint
            self.filtered_error = 0.0
            self.error_mean = 0.0
            self.error_var = 0.0
            self.risk_margin = 0.0
            self.samples = 0

        raw_error = (setpoint - latency) / setpoint
        raw_error = min(1.0, max(-1.0, raw_error))

        # A low latency sample caused by a lack of offered work says nothing
        # about how much concurrency the service can safely handle.  Freezing
        # positive adaptation on those samples prevents load noise from making
        # the controller chase unavailable traffic.  Overload samples always
        # remain actionable.
        demand_limited = (raw_error > 0.0 and
                          admitted < 0.95 * self.limit and
                          arrival <= admitted + 1e-12)

        old_filtered = self.filtered_error
        trend = 0.0 if demand_limited else raw_error - old_filtered
        if not demand_limited:
            self.filtered_error = old_filtered + self._ALPHA * trend

        # Exponentially weighted population variance.  This stable form is
        # non-negative apart from possible last-bit roundoff.
        old_mean = self.error_mean
        if not demand_limited:
            self.error_mean = old_mean + self._ALPHA * (raw_error - old_mean)
            self.error_var = ((1.0 - self._ALPHA) *
                              (self.error_var + self._ALPHA *
                               (raw_error - old_mean) ** 2))
        if self.error_var < 0.0:
            self.error_var = 0.0
        if not demand_limited:
            self.samples += 1

        volatility = math.sqrt(self.error_var)
        new_margin = self._RISK_GAIN * volatility

        # The trend term makes the final observations in a finite sequence
        # count more than the early ones.  Falling latency gives a positive
        # trend and therefore permits more concurrency.
        signal = (0.0 if demand_limited else
                  raw_error + self._TREND_GAIN * trend)
        gain = self._UP_GAIN if signal >= 0.0 else self._DOWN_GAIN
        feedback = gain * signal

        # Apply only the *change* in the risk reserve.  Thus constant variance
        # has a persistent finite cost instead of causing an unbounded drift.
        control_delta = feedback - (new_margin - self.risk_margin)
        proposed = self.limit + control_delta
        self.limit = self._clip(proposed)
        self.risk_margin = new_margin

        if proposed > self.max_limit:
            saturated = 1
        elif proposed < self.min_limit:
            saturated = -1
        else:
            saturated = 0

        # Back-calculation at a hard bound: discard outward-pointing dynamic
        # state rather than storing pressure that cannot affect the actuator.
        # Consequently the first inward move is determined by that new sample,
        # whether saturation lasted for one tick or a million.
        if saturated == 1 and signal >= 0.0:
            self.filtered_error = 0.0
            self.error_mean = 0.0
            self.error_var = 0.0
            self.risk_margin = 0.0
            self.samples = 0
        elif saturated == -1 and signal <= 0.0:
            self.filtered_error = 0.0
            self.error_mean = 0.0
            self.error_var = 0.0
            self.risk_margin = 0.0
            self.samples = 0

        volatility = math.sqrt(self.error_var)

        telemetry = {
            "control_delta": control_delta,
            "demand_limited": int(demand_limited),
            "epoch": self.epoch,
            "filtered_error": self.filtered_error,
            "limit": self.limit,
            "raw_error": raw_error,
            "risk_margin": self.risk_margin,
            "samples": self.samples,
            "saturated": saturated,
            "setpoint_ms": self.setpoint,
            "trend": trend,
            "variance": self.error_var,
            "volatility": volatility,
        }
        return {"limit": self.limit, "telemetry": telemetry}

    def snapshot(self) -> str:
        state = {
            "epoch": self.epoch,
            "error_mean": self.error_mean,
            "error_var": self.error_var,
            "filtered_error": self.filtered_error,
            "limit": self.limit,
            "max_limit": self.max_limit,
            "min_limit": self.min_limit,
            "risk_margin": self.risk_margin,
            "samples": self.samples,
            "setpoint": self.setpoint,
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
            "initial_limit": state["limit"],
        })
        obj.limit = float(state["limit"])
        obj.setpoint = (None if state["setpoint"] is None
                        else float(state["setpoint"]))
        obj.filtered_error = float(state["filtered_error"])
        obj.error_mean = float(state["error_mean"])
        obj.error_var = float(state["error_var"])
        obj.risk_margin = float(state["risk_margin"])
        obj.samples = int(state["samples"])
        obj.epoch = int(state["epoch"])
        return obj
