"""Adaptive concurrency controller.

Tracks a latency setpoint by adjusting a concurrency limit multiplicatively,
driven by a recency-weighted (EMA) relative error with an asymmetric gain:
latency above setpoint (too slow) is corrected more aggressively than latency
below setpoint (headroom), matching classic AIMD-style congestion control.

Anti-windup: the EMA accumulator is frozen (not updated) whenever the limit
is pinned at a bound and the current error would only push further into that
bound. This keeps the first response after sustained saturation bounded by
the triggering observation itself, not by how long saturation lasted.

Setpoint changes reset the EMA to the current tick's error so that all later
corrections are computed purely from post-change observations.
"""

from __future__ import annotations

import json

# EMA weight on the newest observation: higher means more recency weight.
# The decay itself is symmetric; direction asymmetry lives entirely in the
# gains below, so a transient excursion unwinds at the same rate it built
# up (no one-sided windup once conditions recover).
_ALPHA = 0.22
# Asymmetric gains applied to the EMA error: downward (too-slow) correction
# must exceed 2x the upward (headroom) correction for an equal-magnitude
# deviation.
_K_DOWN = 0.4
_K_UP = 0.13
# Guard rails so a single extreme observation cannot produce a nonsensical
# (e.g. negative) multiplicative factor.
_ERR_CLAMP = 3.0
_FACTOR_MIN = 0.2
_FACTOR_MAX = 5.0

_TELEMETRY_KEYS = (
    "t",
    "rel_err",
    "ema_err",
    "gain",
    "factor",
    "saturated",
    "setpoint_changed",
)


def _clamp(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


class Controller:
    def __init__(self, config: dict) -> None:
        self._min_limit = float(config["min_limit"])
        self._max_limit = float(config["max_limit"])
        self._limit = float(config["initial_limit"])
        self._ema_err = 0.0
        self._prev_setpoint: float | None = None
        self._t_count = 0
        self._alpha = _ALPHA
        self._k_down = _K_DOWN
        self._k_up = _K_UP

    def tick(self, obs: dict) -> dict:
        t = int(obs["t"])
        latency = float(obs["latency_ms"])
        setpoint = float(obs["setpoint_ms"])

        rel_err_raw = (latency - setpoint) / setpoint
        rel_err = _clamp(rel_err_raw, -_ERR_CLAMP, _ERR_CLAMP)

        setpoint_changed = 0
        if self._prev_setpoint is not None and setpoint != self._prev_setpoint:
            setpoint_changed = 1
            self._ema_err = rel_err
        self._prev_setpoint = setpoint

        saturated_high = self._limit >= self._max_limit
        saturated_low = self._limit <= self._min_limit
        frozen = (saturated_high and rel_err < 0.0) or (saturated_low and rel_err > 0.0)

        # A sign reversal starts a fresh trend: blending a brand-new-direction
        # observation into a stale opposite-sign average would let old,
        # pre-reversal history keep dictating the gain regime for several
        # more ticks (a primacy bias), which is backwards from the recency
        # requirement. Snap to the new observation instead of blending.
        regime_flip = self._ema_err != 0.0 and (rel_err >= 0.0) != (self._ema_err >= 0.0)

        if setpoint_changed or frozen:
            pass
        elif regime_flip:
            self._ema_err = rel_err
        else:
            self._ema_err = self._alpha * rel_err + (1.0 - self._alpha) * self._ema_err

        gain = self._k_down if self._ema_err >= 0.0 else self._k_up
        factor = 1.0 - gain * self._ema_err
        factor = _clamp(factor, _FACTOR_MIN, _FACTOR_MAX)

        new_limit = _clamp(self._limit * factor, self._min_limit, self._max_limit)
        self._limit = new_limit
        self._t_count += 1

        telemetry = {
            "t": t,
            "rel_err": rel_err,
            "ema_err": self._ema_err,
            "gain": gain,
            "factor": factor,
            "saturated": int(saturated_high or saturated_low),
            "setpoint_changed": setpoint_changed,
        }
        assert set(telemetry.keys()) == set(_TELEMETRY_KEYS)
        return {"limit": self._limit, "telemetry": telemetry}

    def snapshot(self) -> str:
        state = {
            "min_limit": self._min_limit,
            "max_limit": self._max_limit,
            "limit": self._limit,
            "ema_err": self._ema_err,
            "prev_setpoint": self._prev_setpoint,
            "t_count": self._t_count,
            "alpha": self._alpha,
            "k_down": self._k_down,
            "k_up": self._k_up,
        }
        return json.dumps(state, sort_keys=True)

    @classmethod
    def restore(cls, blob: str) -> "Controller":
        state = json.loads(blob)
        obj = cls.__new__(cls)
        obj._min_limit = float(state["min_limit"])
        obj._max_limit = float(state["max_limit"])
        obj._limit = float(state["limit"])
        obj._ema_err = float(state["ema_err"])
        obj._prev_setpoint = (
            None if state["prev_setpoint"] is None else float(state["prev_setpoint"])
        )
        obj._t_count = int(state["t_count"])
        obj._alpha = float(state["alpha"])
        obj._k_down = float(state["k_down"])
        obj._k_up = float(state["k_up"])
        return obj
