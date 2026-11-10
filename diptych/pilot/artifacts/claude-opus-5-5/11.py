"""Adaptive concurrency controller.

Regulates a concurrency limit so that observed latency tracks a setpoint.
Uses only the standard library and pure float arithmetic, so the same
inputs always give bit-identical outputs.
"""

import json
import math


def _clip(x, lo, hi):
    if x < lo:
        return lo
    if x > hi:
        return hi
    return x


def _enc(x):
    if x is None:
        return None
    return float(x).hex()


def _dec(s):
    if s is None:
        return None
    return float.fromhex(s)


class Controller:
    # Gains (constants, not part of persisted state).
    _K_UP = 1.0          # upward step gain (in units of _unit)
    _K_DOWN = 3.0        # downward step gain: strictly > 2 * _K_UP
    _TREND_ALPHA = 0.5   # EWMA weight for latency slope (recency)
    _TREND_GAIN = 0.5    # contribution of trend to control signal
    _VOL_BETA = 0.1      # EWMA weight for error mean / variance
    _VOL_GAIN = 0.5      # additive conservatism per unit of error std
    _CEIL_GAIN = 1.0     # volatility-driven ceiling shrink
    _UNIT_FRAC = 0.03    # step unit as fraction of [min, max] span
    _SNAP = 1e-15        # snap tiny state values to exact zero

    _FLOAT_STATE = ("limit", "trend", "err_mean", "err_var",
                    "last_setpoint", "prev_latency")
    _INT_STATE = ("ticks", "since_reset", "setpoint_resets",
                  "saturated_ticks")

    def __init__(self, config):
        self.min_limit = float(config.get("min_limit", 1.0))
        self.max_limit = float(config.get("max_limit", 200.0))
        if self.max_limit < self.min_limit:
            self.max_limit = self.min_limit
        init = float(config.get("initial_limit", 50.0))
        self.limit = _clip(init, self.min_limit, self.max_limit)
        span = self.max_limit - self.min_limit
        self.unit = max(self._UNIT_FRAC * span, 1e-9)

        self.trend = 0.0
        self.err_mean = 0.0
        self.err_var = 0.0
        self.last_setpoint = None
        self.prev_latency = None

        self.ticks = 0
        self.since_reset = 0
        self.setpoint_resets = 0
        self.saturated_ticks = 0

    # ------------------------------------------------------------------ #
    def _reset_history(self):
        self.trend = 0.0
        self.err_mean = 0.0
        self.err_var = 0.0
        self.prev_latency = None
        self.since_reset = 0
        self.saturated_ticks = 0

    def tick(self, obs):
        latency = float(obs["latency_ms"])
        setpoint = float(obs["setpoint_ms"])
        arrival = float(obs.get("arrival_rps", 0.0))
        admitted = float(obs.get("admitted_rps", 0.0))
        sp = setpoint if setpoint > 1e-9 else 1e-9

        # Setpoint change: discard all history from the old regime.
        setpoint_changed = 0
        if self.last_setpoint is None:
            self.last_setpoint = setpoint
        elif setpoint != self.last_setpoint:
            self._reset_history()
            self.last_setpoint = setpoint
            self.setpoint_resets += 1
            setpoint_changed = 1

        # Normalized error (positive = too slow).
        err = (latency - sp) / sp
        err = _clip(err, -1.0, 10.0)

        # Recency-weighted trend of latency (normalized slope).
        if self.prev_latency is None:
            diff = 0.0
        else:
            diff = _clip((latency - self.prev_latency) / sp, -10.0, 10.0)
        a = self._TREND_ALPHA
        trend_new = (1.0 - a) * self.trend + a * diff

        # Volatility estimate (EWMA mean / variance of error).
        b = self._VOL_BETA
        dev = err - self.err_mean
        var_new = (1.0 - b) * self.err_var + b * dev * dev
        mean_new = self.err_mean + b * dev
        if var_new < self._SNAP:
            var_new = 0.0
        std = math.sqrt(var_new)

        # Control signal and asymmetric step.
        signal = err + self._TREND_GAIN * trend_new + self._VOL_GAIN * std
        signal = _clip(signal, -1.0, 1.0)
        if signal > 0.0:
            step = -self._K_DOWN * self.unit * signal
        else:
            step = -self._K_UP * self.unit * signal

        # Volatility-dependent ceiling.
        ceiling = self.max_limit / (1.0 + self._CEIL_GAIN * std)
        ceiling = _clip(ceiling, self.min_limit, self.max_limit)

        # Anti-windup: when pinned at a bound and pushing further into it,
        # freeze the history so it can't depend on how long we stayed pinned.
        saturated = 0
        if (self.limit >= ceiling and step > 0.0) or \
           (self.limit <= self.min_limit and step < 0.0):
            saturated = 1

        if saturated:
            self.trend = 0.0
            self.saturated_ticks += 1
        else:
            self.trend = 0.0 if abs(trend_new) < self._SNAP else trend_new
            self.err_mean = mean_new
            self.err_var = var_new
            self.saturated_ticks = 0

        prev_limit = self.limit
        new_limit = _clip(prev_limit + step, self.min_limit, ceiling)
        new_limit = _clip(new_limit, self.min_limit, self.max_limit)
        self.limit = new_limit
        self.prev_latency = latency
        self.ticks += 1
        self.since_reset += 1

        if signal > 0.0:
            regime = "decrease"
        elif signal < 0.0:
            regime = "increase"
        else:
            regime = "hold"
        if saturated:
            regime = "saturated"

        shed = arrival - admitted
        if shed < 0.0:
            shed = 0.0

        telemetry = {
            "t": int(obs.get("t", self.ticks - 1)),
            "limit": float(new_limit),
            "prev_limit": float(prev_limit),
            "step": float(new_limit - prev_limit),
            "raw_step": float(step),
            "error": float(err),
            "signal": float(signal),
            "trend": float(self.trend),
            "err_mean": float(self.err_mean),
            "err_std": float(math.sqrt(self.err_var)),
            "ceiling": float(ceiling),
            "setpoint_ms": float(setpoint),
            "latency_ms": float(latency),
            "shed_rps": float(shed),
            "saturated": int(saturated),
            "saturated_ticks": int(self.saturated_ticks),
            "setpoint_changed": int(setpoint_changed),
            "setpoint_resets": int(self.setpoint_resets),
            "since_reset": int(self.since_reset),
            "regime": regime,
        }
        return {"limit": float(new_limit), "telemetry": telemetry}

    # ------------------------------------------------------------------ #
    def snapshot(self):
        data = {
            "v": 1,
            "min_limit": _enc(self.min_limit),
            "max_limit": _enc(self.max_limit),
            "unit": _enc(self.unit),
        }
        for k in self._FLOAT_STATE:
            data[k] = _enc(getattr(self, k))
        for k in self._INT_STATE:
            data[k] = int(getattr(self, k))
        return json.dumps(data, sort_keys=True, separators=(",", ":"))

    @classmethod
    def restore(cls, blob):
        data = json.loads(blob)
        obj = cls.__new__(cls)
        obj.min_limit = _dec(data["min_limit"])
        obj.max_limit = _dec(data["max_limit"])
        obj.unit = _dec(data["unit"])
        for k in cls._FLOAT_STATE:
            setattr(obj, k, _dec(data[k]))
        for k in cls._INT_STATE:
            setattr(obj, k, int(data[k]))
        return obj
