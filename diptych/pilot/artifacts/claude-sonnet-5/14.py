"""Adaptive concurrency controller.

Regulates a concurrency limit so that observed latency tracks a setpoint,
using an exponentially-weighted error/variance estimate (not an unbounded
integral) so that recent observations dominate and saturation never causes
windup.
"""

import json
import math


class Controller:
    # Smoothing factors: higher ALPHA/BETA => more weight on recent ticks.
    ALPHA = 0.3   # error EWMA decay
    BETA = 0.25   # variance EWMA decay
    KU = 0.15     # upward (limit-increasing) gain
    KD = 0.5      # downward (limit-decreasing) gain -- must stay > 2*KU
    KV = 0.4      # volatility-suppression gain

    def __init__(self, config):
        config = config or {}
        self.min_limit = float(config.get("min_limit", 1.0))
        self.max_limit = float(config.get("max_limit", 200.0))
        self.initial_limit = float(config.get("initial_limit", 50.0))

        limit = self.initial_limit
        if limit < self.min_limit:
            limit = self.min_limit
        if limit > self.max_limit:
            limit = self.max_limit
        self.limit = limit

        self.ema_error = 0.0
        self.ema_var = 0.0
        self.last_setpoint = None
        self.ticks_since_change = 0

    def tick(self, obs):
        t = obs["t"]
        latency = float(obs["latency_ms"])
        setpoint = float(obs["setpoint_ms"])
        arrival = float(obs.get("arrival_rps", 0.0))
        admitted = float(obs.get("admitted_rps", 0.0))

        error = latency - setpoint

        setpoint_changed = (self.last_setpoint is None) or (setpoint != self.last_setpoint)
        if setpoint_changed:
            # Drop all history so corrections depend only on post-change data.
            self.ema_error = error
            self.ema_var = 0.0
            self.ticks_since_change = 0
        else:
            diff = error - self.ema_error
            self.ema_var = self.BETA * diff * diff + (1.0 - self.BETA) * self.ema_var
            self.ema_error = self.ALPHA * error + (1.0 - self.ALPHA) * self.ema_error
            self.ticks_since_change += 1
        self.last_setpoint = setpoint

        norm_error = self.ema_error / setpoint if setpoint != 0.0 else 0.0
        std = math.sqrt(self.ema_var) if self.ema_var > 0.0 else 0.0
        norm_std = std / setpoint if setpoint != 0.0 else 0.0

        if norm_error >= 0.0:
            factor = 1.0 - self.KD * norm_error
        else:
            factor = 1.0 - self.KU * norm_error
        if factor < 0.0:
            factor = 0.0

        vol_factor = 1.0 - self.KV * norm_std
        if vol_factor < 0.0:
            vol_factor = 0.0

        new_limit = self.limit * factor * vol_factor
        if new_limit < self.min_limit:
            new_limit = self.min_limit
        if new_limit > self.max_limit:
            new_limit = self.max_limit
        self.limit = new_limit

        telemetry = {
            "t": float(t),
            "limit": self.limit,
            "ema_error_ms": self.ema_error,
            "ema_std_ms": std,
            "norm_error": norm_error,
            "norm_std": norm_std,
            "factor": factor,
            "vol_factor": vol_factor,
            "ticks_since_setpoint_change": float(self.ticks_since_change),
            "raw_error_ms": error,
            "setpoint_ms": setpoint,
            "arrival_rps": arrival,
            "admitted_rps": admitted,
        }
        return {"limit": self.limit, "telemetry": telemetry}

    def snapshot(self):
        state = {
            "min_limit": self.min_limit,
            "max_limit": self.max_limit,
            "initial_limit": self.initial_limit,
            "limit": self.limit,
            "ema_error": self.ema_error,
            "ema_var": self.ema_var,
            "last_setpoint": self.last_setpoint,
            "ticks_since_change": self.ticks_since_change,
        }
        return json.dumps(state, sort_keys=True)

    @classmethod
    def restore(cls, blob):
        state = json.loads(blob)
        obj = cls({
            "min_limit": state["min_limit"],
            "max_limit": state["max_limit"],
            "initial_limit": state["initial_limit"],
        })
        obj.limit = state["limit"]
        obj.ema_error = state["ema_error"]
        obj.ema_var = state["ema_var"]
        obj.last_setpoint = state["last_setpoint"]
        obj.ticks_since_change = state["ticks_since_change"]
        return obj
