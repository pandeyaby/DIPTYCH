"""Adaptive concurrency controller.

Log-domain integral controller with a recency-weighted proportional term,
asymmetric (conservative) gains, a volatility margin, integrator clamping for
anti-windup, and history reset on setpoint change. All state is plain floats
and lists, serialised via JSON (float repr round-trips exactly).
"""

import json
import math

_WINDOW = 20          # observations kept for trend / volatility estimates
_RECENCY = 0.7        # geometric decay of window weights (newest weight 1)
_KI_UP = 0.05         # integrator gain for latency below setpoint
_KI_DOWN = 0.20       # integrator gain for latency above setpoint (4x up)
_KI_DOWN_CAP = 0.25   # max integrator decrease per tick (log units)
_KP_UP = 0.05         # proportional gain, below setpoint
_KP_DOWN = 0.20       # proportional gain, above setpoint (4x up)
_KV_INT = 0.5         # volatility bias added to integrated error
_KV_OUT = 0.5         # volatility margin applied to the output
_ERR_MIN = -1.0
_ERR_MAX = 3.0

_TELEMETRY_KEYS = (
    "tick",
    "error",
    "err_trend",
    "err_std",
    "integrator",
    "p_term",
    "vol_factor",
    "raw_limit",
    "saturated_high",
    "saturated_low",
    "setpoint_ms",
    "setpoint_changed",
    "window_len",
)


def _finite(x):
    return isinstance(x, float) and math.isfinite(x)


def _clamp(x, lo, hi):
    if x < lo:
        return lo
    if x > hi:
        return hi
    return x


class Controller:
    def __init__(self, config: dict):
        min_limit = float(config.get("min_limit", 1.0))
        max_limit = float(config.get("max_limit", 200.0))
        initial = float(config.get("initial_limit", 50.0))
        if not _finite(min_limit) or min_limit <= 0.0:
            min_limit = 1e-9 if not _finite(min_limit) else max(min_limit, 1e-9)
        if not _finite(max_limit) or max_limit < min_limit:
            max_limit = min_limit
        if not _finite(initial):
            initial = min_limit
        initial = _clamp(initial, min_limit, max_limit)

        self._min = min_limit
        self._max = max_limit
        self._initial = initial
        self._log_min = math.log(min_limit)
        self._log_max = math.log(max_limit)

        self._integrator = math.log(initial)
        self._window = []
        self._prev_sp = None
        self._last_limit = initial
        self._ticks = 0

    # ------------------------------------------------------------------ core

    def _window_stats(self):
        n = len(self._window)
        if n == 0:
            return 0.0, 0.0
        num = 0.0
        den = 0.0
        w = 1.0
        for i in range(n - 1, -1, -1):
            num += w * self._window[i]
            den += w
            w *= _RECENCY
        trend = num / den
        if n < 2:
            return trend, 0.0
        mean = math.fsum(self._window) / n
        var = math.fsum((x - mean) * (x - mean) for x in self._window) / n
        return trend, math.sqrt(var) if var > 0.0 else 0.0

    def tick(self, obs: dict) -> dict:
        try:
            sp = float(obs.get("setpoint_ms", float("nan")))
        except (TypeError, ValueError):
            sp = float("nan")
        if not _finite(sp) or sp <= 0.0:
            sp = self._prev_sp if self._prev_sp is not None else 1.0

        try:
            lat = float(obs.get("latency_ms", float("nan")))
        except (TypeError, ValueError):
            lat = float("nan")
        if _finite(lat):
            err = _clamp((lat - sp) / sp, _ERR_MIN, _ERR_MAX)
        else:
            err = _ERR_MAX  # unknown latency: be conservative

        changed = self._prev_sp is not None and sp != self._prev_sp
        if changed:
            # Forget pre-change history; continue from the current limit.
            self._integrator = _clamp(
                math.log(self._last_limit), self._log_min, self._log_max
            )
            self._window = []
        self._prev_sp = sp

        self._window.append(err)
        if len(self._window) > _WINDOW:
            del self._window[0 : len(self._window) - _WINDOW]

        trend, std = self._window_stats()

        # Integral action (asymmetric, volatility-biased, clamped).
        e_int = err + _KV_INT * std
        if e_int > 0.0:
            step = -min(_KI_DOWN * e_int, _KI_DOWN_CAP)
        else:
            step = _KI_UP * (-e_int)
        integ = self._integrator + step
        sat_high = 0
        sat_low = 0
        if integ >= self._log_max:
            integ = self._log_max
            sat_high = 1
        elif integ <= self._log_min:
            integ = self._log_min
            sat_low = 1
        self._integrator = integ

        # Proportional action on recency-weighted trend (asymmetric).
        if trend > 0.0:
            p_term = -_KP_DOWN * trend
        else:
            p_term = _KP_UP * (-trend)

        vol_factor = 1.0 / (1.0 + _KV_OUT * std)

        raw = math.exp(integ + p_term) * vol_factor
        limit = _clamp(raw, self._min, self._max)
        self._last_limit = limit
        tick_idx = self._ticks
        self._ticks += 1

        telemetry = {
            "tick": tick_idx,
            "error": err,
            "err_trend": trend,
            "err_std": std,
            "integrator": integ,
            "p_term": p_term,
            "vol_factor": vol_factor,
            "raw_limit": raw,
            "saturated_high": sat_high,
            "saturated_low": sat_low,
            "setpoint_ms": sp,
            "setpoint_changed": 1 if changed else 0,
            "window_len": len(self._window),
        }
        return {"limit": limit, "telemetry": telemetry}

    # ------------------------------------------------------------ persistence

    def snapshot(self) -> str:
        state = {
            "config": {
                "min_limit": self._min,
                "max_limit": self._max,
                "initial_limit": self._initial,
            },
            "integrator": self._integrator,
            "window": list(self._window),
            "prev_sp": self._prev_sp,
            "last_limit": self._last_limit,
            "ticks": self._ticks,
        }
        return json.dumps(state, sort_keys=True, allow_nan=False)

    @classmethod
    def restore(cls, blob: str) -> "Controller":
        state = json.loads(blob)
        ctl = cls(state["config"])
        ctl._integrator = float(state["integrator"])
        ctl._window = [float(x) for x in state["window"]]
        ps = state["prev_sp"]
        ctl._prev_sp = None if ps is None else float(ps)
        ctl._last_limit = float(state["last_limit"])
        ctl._ticks = int(state["ticks"])
        return ctl
