"""Adaptive concurrency controller.

Tracks latency to a setpoint by adjusting a concurrency limit each tick.

Core signal: an exponentially-weighted moving average ("trend") of the
relative latency error ``(latency_ms - setpoint_ms) / setpoint_ms``. EWMA
weighting makes recent observations dominate (trend recency) while staying
bounded by the range of recently observed errors -- unlike an unbounded
integral, it cannot "wind up" during sustained saturation, so the first
response after saturation is governed by the current error, not by how long
the controller was saturated (anti-windup).

The trend is fed through an asymmetric gain: a positive trend (latency too
high) is scaled by GAIN_DOWN, a negative trend (latency too low) by the much
smaller GAIN_UP, so corrective cuts are strictly larger than corrective
increases of equal magnitude. That same rectifying asymmetry means noisier
(higher-variance) error sequences pull the average limit down more than
calmer ones; an explicit volatility term (EWMA of the squared deviation from
trend) reinforces this directly.

On a setpoint change, the trend/volatility accumulators are reset so that
corrections depend only on observations made after the change.
"""

from __future__ import annotations

import json
import math

ALPHA_FAST = 0.35
ALPHA_SLOW = 0.03
ALPHA_VAR = 0.35
GAIN_DOWN = 0.5
GAIN_UP = 0.15
MOMENTUM_GAIN = 0.7
VOL_GAIN = 0.02
ERROR_CAP = 2.5
DELTA_MIN = -0.6
DELTA_MAX = 0.6

TELEMETRY_KEYS = (
    "t",
    "limit",
    "error",
    "trend",
    "momentum",
    "volatility",
    "delta",
    "setpoint_reset",
    "saturated",
)


class Controller:
    def __init__(self, config: dict) -> None:
        self.min_limit = float(config["min_limit"])
        self.max_limit = float(config["max_limit"])
        self.limit = min(max(float(config["initial_limit"]), self.min_limit), self.max_limit)
        self.trend_fast = 0.0
        self.trend_slow = 0.0
        self.var = 0.0
        self.prev_setpoint: float | None = None
        self.t = -1

    def tick(self, obs: dict) -> dict:
        setpoint = float(obs["setpoint_ms"])
        latency = float(obs["latency_ms"])
        self.t = int(obs["t"])

        reset = 0
        if self.prev_setpoint is not None and setpoint != self.prev_setpoint:
            self.trend_fast = 0.0
            self.trend_slow = 0.0
            self.var = 0.0
            reset = 1
        self.prev_setpoint = setpoint

        raw_error = (latency - setpoint) / setpoint
        if raw_error > ERROR_CAP:
            error = ERROR_CAP
        elif raw_error < -ERROR_CAP:
            error = -ERROR_CAP
        else:
            error = raw_error

        dev = error - self.trend_fast
        self.var = ALPHA_VAR * (dev * dev) + (1.0 - ALPHA_VAR) * self.var
        self.trend_fast = ALPHA_FAST * error + (1.0 - ALPHA_FAST) * self.trend_fast
        self.trend_slow = ALPHA_SLOW * error + (1.0 - ALPHA_SLOW) * self.trend_slow

        volatility = math.sqrt(self.var)
        momentum = self.trend_fast - self.trend_slow

        if self.trend_fast >= 0.0:
            delta = -GAIN_DOWN * self.trend_fast
        else:
            delta = GAIN_UP * (-self.trend_fast)
        delta -= MOMENTUM_GAIN * momentum
        delta -= VOL_GAIN * volatility

        if delta < DELTA_MIN:
            delta = DELTA_MIN
        elif delta > DELTA_MAX:
            delta = DELTA_MAX

        new_limit = self.limit * (1.0 + delta)
        if new_limit < self.min_limit:
            new_limit = self.min_limit
        elif new_limit > self.max_limit:
            new_limit = self.max_limit
        self.limit = new_limit

        saturated = 1 if (self.limit <= self.min_limit or self.limit >= self.max_limit) else 0

        telemetry = {
            "t": self.t,
            "limit": self.limit,
            "error": error,
            "trend": self.trend_fast,
            "momentum": momentum,
            "volatility": volatility,
            "delta": delta,
            "setpoint_reset": reset,
            "saturated": saturated,
        }
        return {"limit": self.limit, "telemetry": telemetry}

    def snapshot(self) -> str:
        data = {
            "min_limit": self.min_limit,
            "max_limit": self.max_limit,
            "limit": self.limit,
            "trend_fast": self.trend_fast,
            "trend_slow": self.trend_slow,
            "var": self.var,
            "prev_setpoint": self.prev_setpoint,
            "t": self.t,
        }
        return json.dumps(data)

    @classmethod
    def restore(cls, blob: str) -> "Controller":
        data = json.loads(blob)
        obj = cls(
            {
                "min_limit": data["min_limit"],
                "max_limit": data["max_limit"],
                "initial_limit": data["limit"],
            }
        )
        obj.limit = data["limit"]
        obj.trend_fast = data["trend_fast"]
        obj.trend_slow = data["trend_slow"]
        obj.var = data["var"]
        obj.prev_setpoint = data["prev_setpoint"]
        obj.t = data["t"]
        return obj
