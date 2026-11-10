"""Adaptive concurrency controller.

Regulates a concurrency limit so that observed latency tracks a latency
setpoint, using:

  - a recency-weighted (EWMA) error signal for trend sensitivity,
  - a leaky, anti-windup-clamped integral for steady-state tracking,
  - a volatility estimate that biases the limit downward when the error
    signal is noisy, and
  - an asymmetric gain applied to the combined signal so that downward
    corrections are much stronger than upward ones.

Only the standard library (``math``, ``json``) is used; there is no I/O,
threading, randomness, or wall-clock access, so behavior is a pure
function of (config, observation sequence).
"""

from __future__ import annotations

import json
import math

# EWMA decay for the trend-sensitive error signal (higher = faster forgetting
# of older observations -> more recency weight).
ALPHA_E = 0.30

# EWMA decay for the volatility (noise) estimate. Faster than ALPHA_E so a
# transient spike's contribution to the volatility estimate also dissipates
# quickly instead of continuing to depress the limit after the error itself
# has returned to normal.
ALPHA_V = 0.20

# Leak factor for the integral accumulator; < 1 so it cannot wind up without
# bound and so recent terms dominate (same recency property as ALPHA_E).
INTEGRAL_DECAY = 0.95

# Weights combining the error signal, integral term, and volatility estimate
# into one raw control signal.
ERR_WEIGHT = 0.50
INT_WEIGHT = 0.02
VOL_WEIGHT = 0.30

# Asymmetric gains applied to the raw control signal: a positive signal
# (latency above setpoint) is corrected much harder than a negative one
# (latency below setpoint), satisfying the >2x asymmetry requirement with
# margin (0.60 / 0.20 = 3x).
DOWN_GAIN = 0.60
UP_GAIN = 0.20

# Relative error is clipped before feeding any internal state. Latency is a
# steeply convex function of admitted load near capacity, so a single tick of
# severe overload can otherwise produce an enormous instantaneous error that
# would blow the limit straight down to min_limit in one step. Clipping
# bounds the reaction to any one tick while leaving ordinary (non-pathological)
# deviations, which stay well inside this range, uninfluenced.
ERROR_CLIP = 1.0

EPS = 1e-9

_TELEMETRY_KEYS = (
    "t",
    "error",
    "ewma_error",
    "integral",
    "vol_std",
    "raw_signal",
    "delta_frac",
    "limit",
    "saturated",
)


class Controller:
    def __init__(self, config: dict) -> None:
        self._min_limit = float(config["min_limit"])
        self._max_limit = float(config["max_limit"])
        self._limit = float(config["initial_limit"])
        self._ewma_err = 0.0
        self._integral = 0.0
        self._vol_ewma = 0.0
        self._setpoint = None  # last-seen setpoint, for change detection

    def tick(self, obs: dict) -> dict:
        t = obs["t"]
        latency = float(obs["latency_ms"])
        setpoint = float(obs["setpoint_ms"])

        # Setpoint-change quiet: wipe every piece of state that feeds into
        # the correction so it depends only on observations made after the
        # change, not on history from before it.
        if self._setpoint is not None and setpoint != self._setpoint:
            self._ewma_err = 0.0
            self._integral = 0.0
            self._vol_ewma = 0.0
        self._setpoint = setpoint

        e = (latency - setpoint) / setpoint
        e_eff = max(-ERROR_CLIP, min(ERROR_CLIP, e))

        self._ewma_err = ALPHA_E * e_eff + (1.0 - ALPHA_E) * self._ewma_err

        # Anti-windup: freeze the integral while saturated *and* the error
        # would push further into that same saturation direction, so a
        # sustained saturation never inflates the accumulator. The moment
        # the error points back the other way (an excursion), the
        # accumulator resumes updating from wherever it was frozen -
        # bounded by that one tick's error, not by how long it was frozen.
        at_max = self._limit >= self._max_limit - EPS
        at_min = self._limit <= self._min_limit + EPS
        frozen = (at_max and e_eff < 0.0) or (at_min and e_eff > 0.0)
        if not frozen:
            self._integral = INTEGRAL_DECAY * self._integral + e_eff

        # Volatility: EWMA of the squared deviation of the instantaneous
        # error from its own trend. Pure noise/variance measure, independent
        # of the sign or level of the error itself.
        dev = e_eff - self._ewma_err
        self._vol_ewma = ALPHA_V * (dev * dev) + (1.0 - ALPHA_V) * self._vol_ewma
        vol_std = math.sqrt(self._vol_ewma)

        raw = ERR_WEIGHT * self._ewma_err + INT_WEIGHT * self._integral + VOL_WEIGHT * vol_std

        gain = DOWN_GAIN if raw >= 0.0 else UP_GAIN
        delta_frac = -gain * raw

        new_limit = self._limit * (1.0 + delta_frac)
        new_limit = min(max(new_limit, self._min_limit), self._max_limit)
        saturated = 1 if (new_limit <= self._min_limit + EPS or new_limit >= self._max_limit - EPS) else 0
        self._limit = new_limit

        telemetry = {
            "t": int(t),
            "error": e,
            "ewma_error": self._ewma_err,
            "integral": self._integral,
            "vol_std": vol_std,
            "raw_signal": raw,
            "delta_frac": delta_frac,
            "limit": self._limit,
            "saturated": saturated,
        }
        assert set(telemetry.keys()) == set(_TELEMETRY_KEYS)

        return {"limit": self._limit, "telemetry": telemetry}

    def snapshot(self) -> str:
        return json.dumps(
            {
                "min_limit": self._min_limit,
                "max_limit": self._max_limit,
                "limit": self._limit,
                "ewma_err": self._ewma_err,
                "integral": self._integral,
                "vol_ewma": self._vol_ewma,
                "setpoint": self._setpoint,
            }
        )

    @classmethod
    def restore(cls, blob: str) -> "Controller":
        data = json.loads(blob)
        obj = cls.__new__(cls)
        obj._min_limit = data["min_limit"]
        obj._max_limit = data["max_limit"]
        obj._limit = data["limit"]
        obj._ewma_err = data["ewma_err"]
        obj._integral = data["integral"]
        obj._vol_ewma = data["vol_ewma"]
        obj._setpoint = data["setpoint"]
        return obj
