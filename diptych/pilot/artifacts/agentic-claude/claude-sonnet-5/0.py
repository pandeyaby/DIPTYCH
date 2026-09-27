"""Adaptive concurrency controller.

Normalised error e = (setpoint - latency) / setpoint  (positive = headroom).

Each tick the limit is scaled by (1 + gain * c), where c is the error plus a
recency-weighted trend (fast EWMA minus slow EWMA of e) and minus a volatility
penalty from the coefficient of variation of offered load.  The gain is
GAIN_DOWN when c < 0 and GAIN_UP when c > 0 (GAIN_DOWN > 2 * GAIN_UP).

There is no integrator, so nothing can wind up while the limit sits at a
bound; the trend term is additionally clipped relative to the current error.
All filters are reset when the setpoint changes.
"""

import json
import math

GAIN_UP = 0.03
GAIN_DOWN = 0.09  # 3x GAIN_UP: strictly more than twice
ALPHA_FAST = 0.6
ALPHA_SLOW = 0.2
TREND_GAIN = 1.0
TREND_CLIP = 0.5  # trend contribution at most this fraction of |e|
ALPHA_VAR = 0.1
VOL_GAIN = 0.3
E_CLIP = 1.0


def _finite(x, default):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return default
    return x if math.isfinite(x) else default


class Controller:
    def __init__(self, config: dict):
        self.min_limit = float(config.get("min_limit", 1.0))
        self.max_limit = float(config.get("max_limit", 200.0))
        init = float(config.get("initial_limit", 50.0))
        self.limit = min(max(init, self.min_limit), self.max_limit)
        self.setpoint = None
        self._reset_filters()

    def _reset_filters(self):
        self.fast = 0.0
        self.slow = 0.0
        self.n = 0
        self.arr_mean = 0.0
        self.arr_var = 0.0

    def tick(self, obs: dict) -> dict:
        sp = _finite(obs.get("setpoint_ms"), 0.0)
        lat = _finite(obs.get("latency_ms"), sp)
        arr = max(_finite(obs.get("arrival_rps"), 0.0), 0.0)
        if sp <= 0.0:
            sp = max(lat, 1e-9)

        if self.setpoint is None or sp != self.setpoint:
            self._reset_filters()
            self.setpoint = sp

        e = min(max((sp - lat) / sp, -E_CLIP), E_CLIP)

        # Volatility penalty from state *before* this sample.
        if self.arr_mean > 1e-9:
            cv = math.sqrt(self.arr_var) / self.arr_mean
        else:
            cv = 0.0
        penalty = VOL_GAIN * cv

        # Recency-weighted trend.
        if self.n == 0:
            self.fast = e
            self.slow = e
            self.arr_mean = arr
            self.arr_var = 0.0
        else:
            self.fast += ALPHA_FAST * (e - self.fast)
            self.slow += ALPHA_SLOW * (e - self.slow)
            d = arr - self.arr_mean
            self.arr_mean += ALPHA_VAR * d
            self.arr_var = (1.0 - ALPHA_VAR) * (self.arr_var + ALPHA_VAR * d * d)
        self.n += 1

        bound = TREND_CLIP * abs(e)
        trend = min(max(TREND_GAIN * (self.fast - self.slow), -bound), bound)
        c = e - penalty + trend

        gain = GAIN_UP if c > 0.0 else GAIN_DOWN
        new = self.limit * (1.0 + gain * c)
        self.limit = min(max(new, self.min_limit), self.max_limit)

        return {
            "limit": self.limit,
            "telemetry": {
                "error": e,
                "trend": trend,
                "volatility_cv": cv,
                "correction": c,
                "ewma_fast": self.fast,
                "ewma_slow": self.slow,
                "setpoint_ms": sp,
                "samples": self.n,
            },
        }

    def snapshot(self) -> str:
        state = {
            "limit": self.limit,
            "min_limit": self.min_limit,
            "max_limit": self.max_limit,
            "setpoint": self.setpoint,
            "fast": self.fast,
            "slow": self.slow,
            "n": self.n,
            "arr_mean": self.arr_mean,
            "arr_var": self.arr_var,
        }
        return json.dumps(state, sort_keys=True)

    @classmethod
    def restore(cls, blob: str) -> "Controller":
        s = json.loads(blob)
        self = cls({"min_limit": s["min_limit"], "max_limit": s["max_limit"],
                    "initial_limit": s["limit"]})
        self.limit = s["limit"]
        self.setpoint = s["setpoint"]
        self.fast = s["fast"]
        self.slow = s["slow"]
        self.n = s["n"]
        self.arr_mean = s["arr_mean"]
        self.arr_var = s["arr_var"]
        return self
