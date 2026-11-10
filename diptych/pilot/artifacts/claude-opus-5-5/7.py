"""Adaptive concurrency controller.

Each tick the controller takes one latency observation and returns a new
concurrency limit. The limit has two parts:

* An integral term, ``base``. Each tick it is multiplied by a factor that
  depends on the normalized error ``e = (setpoint - latency) / setpoint``,
  minus a penalty that grows with error volatility. Positive errors raise it
  with gain ``K_UP``. Negative errors cut it with the larger gain ``K_DOWN``.
* A proportional term on a fast EWMA of the error. It gives recent
  observations more weight than older ones and uses the same asymmetric gains.

Anti-windup: ``base`` is clamped to the limit bounds every tick. While it is
pinned at a bound, trend memory that would push further past that bound is
dropped.

Setpoint change: the last returned limit becomes ``base``, and all error
statistics are reset. From then on, corrections depend only on new
observations.

The module uses only plain float arithmetic, with no hashing, randomness or
clock access, so results are deterministic. Snapshots store floats as hex, so
a restored controller makes bit-identical decisions.
"""

import json
import math

K_UP = 0.09      # integral gain when latency is below setpoint
K_DOWN = 0.30    # integral gain when latency is above setpoint (> 2 * K_UP)
KP = 1.0         # proportional gain on the fast error EWMA
ALPHA = 0.4      # fast EWMA coefficient for the trend term
BETA = 0.1       # EWMA coefficient for the volatility statistics
KV = 0.5         # volatility penalty gain
E_CLIP = 1.0     # clip normalized error to [-E_CLIP, E_CLIP]

_FLOAT_FIELDS = (
    "min_limit", "max_limit", "base", "last_limit",
    "ef", "mean", "var", "prev_sp",
)
_INT_FIELDS = ("n", "has_sp", "resets")


def _gain(x):
    """Asymmetric gain: corrections downward are stronger than upward."""
    if x > 0.0:
        return x * K_UP
    return x * K_DOWN


class Controller:
    def __init__(self, config):
        self.min_limit = float(config.get("min_limit", 1.0))
        self.max_limit = float(config.get("max_limit", 200.0))
        if self.max_limit < self.min_limit:
            self.max_limit = self.min_limit
        init = float(config.get("initial_limit", 50.0))
        init = self._clamp(init)
        self.base = init
        self.last_limit = init
        self.ef = 0.0
        self.mean = 0.0
        self.var = 0.0
        self.prev_sp = 0.0
        self.has_sp = 0
        self.n = 0
        self.resets = 0

    def _clamp(self, x):
        if x != x:  # NaN guard
            return self.min_limit
        if x < self.min_limit:
            return self.min_limit
        if x > self.max_limit:
            return self.max_limit
        return x

    def tick(self, obs):
        latency = float(obs.get("latency_ms", 0.0))
        sp = float(obs.get("setpoint_ms", 0.0))

        # Setpoint change: fold the current output into the integrator and
        # discard all history so that corrections depend only on new data.
        if self.has_sp and sp != self.prev_sp:
            self.base = self.last_limit
            self.ef = 0.0
            self.mean = 0.0
            self.var = 0.0
            self.n = 0
            self.resets += 1
        self.prev_sp = sp
        self.has_sp = 1

        # Normalized error (positive = headroom, negative = overload).
        if sp > 0.0 and latency == latency:
            e = (sp - latency) / sp
        else:
            e = -E_CLIP
        if e > E_CLIP:
            e = E_CLIP
        elif e < -E_CLIP:
            e = -E_CLIP

        # Volatility statistics (EW mean / variance of the error).
        d = e - self.mean
        self.mean = self.mean + BETA * d
        self.var = (1.0 - BETA) * (self.var + BETA * d * d)
        sigma = math.sqrt(self.var) if self.var > 0.0 else 0.0
        penalty = KV * sigma

        # Fast trend EWMA (recency-weighted).
        self.ef = ALPHA * e + (1.0 - ALPHA) * self.ef
        self.n += 1

        # Integral update with clamping (anti-windup).
        u = _gain(e - penalty)
        self.base = self._clamp(self.base * (1.0 + u))

        # Conditional integration: drop trend memory that pushes past a bound.
        saturated = 0
        if self.base >= self.max_limit:
            saturated = 1
            if self.ef > 0.0:
                self.ef = 0.0
        elif self.base <= self.min_limit:
            saturated = -1
            if self.ef < 0.0:
                self.ef = 0.0

        p = _gain(KP * self.ef)
        limit = self._clamp(self.base * (1.0 + p))
        self.last_limit = limit

        telemetry = {
            "error": e,
            "trend_ewma": self.ef,
            "err_mean": self.mean,
            "err_sigma": sigma,
            "vol_penalty": penalty,
            "integral_step": u,
            "prop_factor": p,
            "base_limit": self.base,
            "limit": limit,
            "setpoint_ms": sp,
            "saturated": saturated,
            "ticks_since_reset": self.n,
            "setpoint_resets": self.resets,
        }
        return {"limit": limit, "telemetry": telemetry}

    def snapshot(self):
        state = {"v": 1}
        for k in _FLOAT_FIELDS:
            state[k] = float(getattr(self, k)).hex()
        for k in _INT_FIELDS:
            state[k] = int(getattr(self, k))
        return json.dumps(state, sort_keys=True, separators=(",", ":"))

    @classmethod
    def restore(cls, blob):
        state = json.loads(blob)
        obj = cls.__new__(cls)
        for k in _FLOAT_FIELDS:
            setattr(obj, k, float.fromhex(state[k]))
        for k in _INT_FIELDS:
            setattr(obj, k, int(state[k]))
        return obj
