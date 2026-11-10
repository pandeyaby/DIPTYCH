"""Adaptive concurrency controller.

Regulates a concurrency limit so that observed latency tracks a setpoint,
using an asymmetric, recency-weighted error signal with anti-windup freezing
and variance-aware throttling of upward growth.
"""

import json


class Controller:
    ALPHA = 0.3        # recency weight for the error EWMA
    BETA = 0.2         # recency weight for the latency mean/variance EWMA
    DOWN_GAIN = 0.9    # gain applied when shrinking the limit
    UP_GAIN = 0.4      # gain applied when growing the limit
    VOL_GAIN = 4.0     # strength of variance-based throttling of growth
    EPS = 1e-9

    def __init__(self, config: dict):
        self.min_limit = float(config.get("min_limit", 1.0))
        self.max_limit = float(config.get("max_limit", 200.0))
        self.initial_limit = float(config.get("initial_limit", 50.0))

        self.limit = self._clamp(self.initial_limit)
        self.ewma_err = 0.0
        self.ewma_mean = None
        self.ewma_var = 0.0
        self.prev_setpoint = None
        self.last_t = None

    def _clamp(self, value: float) -> float:
        return max(self.min_limit, min(self.max_limit, value))

    def tick(self, obs: dict) -> dict:
        t = obs["t"]
        latency = float(obs["latency_ms"])
        setpoint = float(obs["setpoint_ms"])

        if self.ewma_mean is None:
            self.ewma_mean = latency

        setpoint_changed = self.prev_setpoint is not None and setpoint != self.prev_setpoint
        if setpoint_changed:
            self.ewma_err = 0.0
            self.ewma_mean = latency
            self.ewma_var = 0.0
        self.prev_setpoint = setpoint

        error = latency - setpoint

        diff = latency - self.ewma_mean
        self.ewma_mean += self.BETA * diff
        self.ewma_var = (1.0 - self.BETA) * (self.ewma_var + self.BETA * diff * diff)

        at_max = self.limit >= self.max_limit - self.EPS
        at_min = self.limit <= self.min_limit + self.EPS
        want_increase = error < 0.0
        want_decrease = error > 0.0
        frozen = (at_max and want_increase) or (at_min and want_decrease)

        if not frozen:
            self.ewma_err = self.ALPHA * error + (1.0 - self.ALPHA) * self.ewma_err

        if setpoint != 0.0:
            rel_err = self.ewma_err / setpoint
            vol_ratio = self.ewma_var / (setpoint * setpoint)
        else:
            rel_err = self.ewma_err
            vol_ratio = self.ewma_var

        vol_penalty = 1.0 / (1.0 + self.VOL_GAIN * vol_ratio)

        if rel_err > 0.0:
            delta = -self.DOWN_GAIN * rel_err * self.limit
        else:
            delta = self.UP_GAIN * (-rel_err) * self.limit * vol_penalty

        new_limit = self._clamp(self.limit + delta)
        self.limit = new_limit
        self.last_t = t

        telemetry = {
            "t": int(t),
            "limit": self.limit,
            "error": error,
            "ewma_error": self.ewma_err,
            "rel_error": rel_err,
            "ewma_mean_latency": self.ewma_mean,
            "ewma_variance": self.ewma_var,
            "vol_penalty": vol_penalty,
            "delta": delta,
            "frozen": int(frozen),
            "at_max": int(at_max),
            "at_min": int(at_min),
            "setpoint_changed": int(setpoint_changed),
            "setpoint_ms": setpoint,
        }

        return {"limit": self.limit, "telemetry": telemetry}

    def snapshot(self) -> str:
        data = {
            "min_limit": self.min_limit,
            "max_limit": self.max_limit,
            "initial_limit": self.initial_limit,
            "limit": self.limit,
            "ewma_err": self.ewma_err,
            "ewma_mean": self.ewma_mean,
            "ewma_var": self.ewma_var,
            "prev_setpoint": self.prev_setpoint,
            "last_t": self.last_t,
        }
        return json.dumps(data, sort_keys=True)

    @classmethod
    def restore(cls, blob: str) -> "Controller":
        data = json.loads(blob)
        obj = cls({
            "min_limit": data["min_limit"],
            "max_limit": data["max_limit"],
            "initial_limit": data["initial_limit"],
        })
        obj.limit = data["limit"]
        obj.ewma_err = data["ewma_err"]
        obj.ewma_mean = data["ewma_mean"]
        obj.ewma_var = data["ewma_var"]
        obj.prev_setpoint = data["prev_setpoint"]
        obj.last_t = data["last_t"]
        return obj
