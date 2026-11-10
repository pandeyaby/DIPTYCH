"""Adaptive concurrency limit controller.

Design
------
The controller keeps two pieces of state:

* ``baseline`` -- a slowly-updated estimate of the sustainable concurrency
  level (a low-pass filter of the limit actually applied).
* ``ewma_error`` / ``var_ewma`` -- a fast, recency-weighted estimate of the
  relative latency error and its volatility.

Each tick, the applied limit is ``baseline`` scaled by a multiplicative
``shape`` factor derived from the *current* trend/volatility state:

    limit = clamp(baseline * exp(-gain(trend) * trend - kv * volatility))

Because ``shape`` depends only on the present trend state (not on an
accumulated path of past corrections) and ``baseline`` moves slowly, the
outcome of a short run is dominated by where the trend ended up, not by how
it got there -- which is what makes recent observations dominate older ones.
``gain`` is asymmetric (larger when backing off than when growing), and the
volatility term only ever pushes the shape factor down, never flips which
gain applies. An anti-windup freeze stops the trend, volatility, and
baseline estimators from drifting further while the limit is saturated and
the error still points into the saturated direction, so the first response
to a subsequent excursion is governed by its own magnitude rather than by
how long the controller sat at the bound.
"""

from __future__ import annotations

import json
import math


class Controller:
    _ALPHA = 0.6         # EWMA smoothing for the error trend (recency weighting)
    _BETA = 0.15         # EWMA smoothing for the volatility (variance) estimate
    _SLOW_ALPHA = 0.1    # smoothing for the slow sustainable-level baseline
    _GAIN_DOWN = 0.55    # gain applied when latency is at/above setpoint (shrink)
    _GAIN_UP = 0.17       # gain applied when latency is below setpoint (grow)
    _KV = 0.3             # volatility suppression coefficient
    _TREND_CLIP = 1.0     # clip on the trend value used in the shape exponent,
                          # so a single extreme excursion cannot produce an
                          # unboundedly large one-tick correction
    _DEV_CLIP = 1.0       # clip applied to the error used for the variance estimate,
                          # so a single extreme excursion cannot inflate the
                          # volatility estimate for many ticks afterward

    def __init__(self, config: dict) -> None:
        self.min_limit = float(config["min_limit"])
        self.max_limit = float(config["max_limit"])
        self.limit = float(config["initial_limit"])
        self.baseline = float(config["initial_limit"])

        self.ewma_error = 0.0
        self.var_ewma = 0.0
        self.last_setpoint = None
        self.clamped_dir = 0  # -1 / 0 / +1: direction the previous command was clamped in
        self.initialized = False

    def tick(self, obs: dict) -> dict:
        latency = float(obs["latency_ms"])
        setpoint = float(obs["setpoint_ms"])
        error = (latency - setpoint) / setpoint

        is_setpoint_change = self.initialized and self.last_setpoint is not None and setpoint != self.last_setpoint
        reset = (not self.initialized) or is_setpoint_change
        self.last_setpoint = setpoint

        frozen = False
        if reset:
            self.ewma_error = error
            self.var_ewma = 0.0
            self.clamped_dir = 0
            self.initialized = True
        else:
            frozen = (self.clamped_dir > 0 and error <= 0.0) or (self.clamped_dir < 0 and error >= 0.0)
            if not frozen:
                var_prior = self.var_ewma
                clipped_error = max(-self._DEV_CLIP, min(self._DEV_CLIP, error))
                deviation = clipped_error - self.ewma_error
                self.ewma_error = self._ALPHA * error + (1.0 - self._ALPHA) * self.ewma_error
                self.var_ewma = self._BETA * (deviation * deviation) + (1.0 - self._BETA) * var_prior

        volatility = math.sqrt(self.var_ewma) if self.var_ewma > 0.0 else 0.0

        # Direction and magnitude of the shape exponent come from the trend
        # alone, so the asymmetric response and recency weighting stay
        # uncontaminated by the (always non-negative) volatility term, which
        # only ever biases the result toward a smaller limit.
        trend = max(-self._TREND_CLIP, min(self._TREND_CLIP, self.ewma_error))
        gain = self._GAIN_DOWN if trend >= 0.0 else self._GAIN_UP
        exponent = -gain * trend - self._KV * volatility
        shape = math.exp(exponent)

        raw_limit = self.baseline * shape

        if raw_limit > self.max_limit:
            self.clamped_dir = 1
        elif raw_limit < self.min_limit:
            self.clamped_dir = -1
        else:
            self.clamped_dir = 0

        new_limit = min(max(raw_limit, self.min_limit), self.max_limit)
        self.limit = new_limit

        if not frozen:
            self.baseline = self._SLOW_ALPHA * new_limit + (1.0 - self._SLOW_ALPHA) * self.baseline

        telemetry = {
            "limit": new_limit,
            "error": error,
            "ewma_error": self.ewma_error,
            "baseline": self.baseline,
            "volatility": volatility,
            "shape": shape,
            "gain": gain,
            "frozen": int(frozen),
            "clamped_dir": int(self.clamped_dir),
        }
        return {"limit": new_limit, "telemetry": telemetry}

    def snapshot(self) -> str:
        state = {
            "min_limit": self.min_limit,
            "max_limit": self.max_limit,
            "limit": self.limit,
            "baseline": self.baseline,
            "ewma_error": self.ewma_error,
            "var_ewma": self.var_ewma,
            "last_setpoint": self.last_setpoint,
            "clamped_dir": self.clamped_dir,
            "initialized": self.initialized,
        }
        return json.dumps(state)

    @classmethod
    def restore(cls, blob: str) -> "Controller":
        state = json.loads(blob)
        obj = cls.__new__(cls)
        obj.min_limit = state["min_limit"]
        obj.max_limit = state["max_limit"]
        obj.limit = state["limit"]
        obj.baseline = state["baseline"]
        obj.ewma_error = state["ewma_error"]
        obj.var_ewma = state["var_ewma"]
        obj.last_setpoint = state["last_setpoint"]
        obj.clamped_dir = state["clamped_dir"]
        obj.initialized = state["initialized"]
        return obj
