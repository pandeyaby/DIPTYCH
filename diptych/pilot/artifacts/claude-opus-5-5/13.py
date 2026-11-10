"""Adaptive concurrency controller.

It uses an additive, asymmetric correction. The driving signal is a
recency-weighted latency error plus a trend term, plus a penalty that grows
with latency volatility. The filter state resets when the setpoint changes.
While the limit sits at a bound, the filter state is cleared so saturation
cannot wind it up.
"""

import json
import math


class Controller:
    # Tuning constants (fixed, deterministic)
    _ALPHA = 0.5        # EWMA weight of the newest error sample
    _KD = 2.0           # trend gain; > (1-ALPHA)/ALPHA so recent samples dominate
    _BETA = 0.1         # EWMA weight for volatility estimation
    _KV = 0.5           # volatility penalty (pushes toward a lower limit)
    _GAIN_UP = 0.04     # fraction of span per unit signal when raising
    _GAIN_DOWN = 0.13   # fraction of span per unit signal when lowering (>2x up)
    _ERR_CLAMP = 1.0    # bound on relative error magnitude

    _STATE_FIELDS = (
        "min_limit", "max_limit", "limit", "ef", "ef_prev",
        "vol_mean", "vol_var", "last_sp", "since_sp",
    )

    def __init__(self, config: dict):
        self.min_limit = float(config.get("min_limit", 1.0))
        self.max_limit = float(config.get("max_limit", 200.0))
        if self.max_limit < self.min_limit:
            self.max_limit = self.min_limit
        init = float(config.get("initial_limit", 50.0))
        self.limit = self._clamp(init)
        self.ef = 0.0
        self.ef_prev = 0.0
        self.vol_mean = 0.0
        self.vol_var = 0.0
        self.last_sp = None
        self.since_sp = 0

    # ------------------------------------------------------------------ helpers
    def _clamp(self, x):
        if x != x:  # NaN guard
            x = self.min_limit
        if x < self.min_limit:
            return self.min_limit
        if x > self.max_limit:
            return self.max_limit
        return x

    def _reset_history(self):
        self.ef = 0.0
        self.ef_prev = 0.0
        self.vol_mean = 0.0
        self.vol_var = 0.0
        self.since_sp = 0

    # --------------------------------------------------------------------- tick
    def tick(self, obs: dict) -> dict:
        lat = float(obs.get("latency_ms", 0.0))
        sp = float(obs.get("setpoint_ms", 1.0))
        sp_eff = sp if sp > 1e-9 else 1e-9

        sp_changed = 0
        if self.last_sp is None:
            self.last_sp = sp
        elif sp != self.last_sp:
            self.last_sp = sp
            self._reset_history()
            sp_changed = 1

        # Relative error: positive means too slow, so lower the limit.
        if lat != lat:
            lat = sp_eff
        e = (lat - sp_eff) / sp_eff
        c = self._ERR_CLAMP
        if e > c:
            e = c
        elif e < -c:
            e = -c

        # Recency-weighted level and trend.
        ef_old = self.ef
        ef_new = self._ALPHA * e + (1.0 - self._ALPHA) * ef_old
        trend = ef_new - ef_old

        # Volatility estimate (EWMA mean/variance of the error).
        d = e - self.vol_mean
        self.vol_mean += self._BETA * d
        self.vol_var = (1.0 - self._BETA) * (self.vol_var + self._BETA * d * d)
        if self.vol_var < 0.0:
            self.vol_var = 0.0
        std = math.sqrt(self.vol_var)

        u = ef_new + self._KD * trend + self._KV * std

        span = self.max_limit - self.min_limit
        if span <= 0.0:
            span = 1.0
        if u > 0.0:
            step = -self._GAIN_DOWN * u * span
            mode = "decrease"
        elif u < 0.0:
            step = -self._GAIN_UP * u * span
            mode = "increase"
        else:
            step = 0.0
            mode = "hold"

        new_limit = self._clamp(self.limit + step)

        self.ef_prev = ef_old
        self.ef = ef_new

        # Anti-windup: clear the filter state that pushes against a saturated bound.
        saturated = 0
        if new_limit >= self.max_limit and self.ef < 0.0:
            self.ef = 0.0
            self.ef_prev = 0.0
            saturated = 1
        elif new_limit <= self.min_limit and self.ef > 0.0:
            self.ef = 0.0
            self.ef_prev = 0.0
            saturated = -1
        elif new_limit >= self.max_limit:
            saturated = 1
        elif new_limit <= self.min_limit:
            saturated = -1

        self.limit = new_limit
        self.since_sp += 1

        telemetry = {
            "limit": float(new_limit),
            "error": float(e),
            "ef": float(self.ef),
            "trend": float(trend),
            "volatility": float(std),
            "signal": float(u),
            "step": float(step),
            "saturated": int(saturated),
            "setpoint_ms": float(sp),
            "setpoint_changed": int(sp_changed),
            "ticks_since_setpoint_change": int(self.since_sp),
            "mode": mode,
        }
        return {"limit": float(new_limit), "telemetry": telemetry}

    # ---------------------------------------------------------- serialization
    def snapshot(self) -> str:
        state = {}
        for name in self._STATE_FIELDS:
            v = getattr(self, name)
            if v is None:
                state[name] = None
            elif isinstance(v, int) and not isinstance(v, bool) and name == "since_sp":
                state[name] = v
            else:
                state[name] = float(v).hex()
        return json.dumps(state, sort_keys=True, separators=(",", ":"))

    @classmethod
    def restore(cls, blob: str) -> "Controller":
        state = json.loads(blob)
        obj = cls.__new__(cls)
        for name in cls._STATE_FIELDS:
            v = state.get(name)
            if name == "since_sp":
                setattr(obj, name, int(v) if v is not None else 0)
            elif v is None:
                setattr(obj, name, None)
            else:
                setattr(obj, name, float.fromhex(v))
        return obj
