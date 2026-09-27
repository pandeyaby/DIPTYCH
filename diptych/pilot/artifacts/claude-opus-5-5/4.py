"""Adaptive concurrency controller.

Uses a multiplicative, asymmetric proportional law on an exponentially
weighted relative latency error. It adds a trend term (recent change in the
smoothed error), a volatility penalty (EWMA standard deviation of the error),
conditional integration at the upper bound (anti-windup), and a full history
reset whenever the setpoint changes. There is no integral accumulator, so
windup can only come from the smoothing state, and that state is pinned
while the limit is saturated.
"""

import json
import math

_STATE_VERSION = 1

# Gains
_ALPHA = 0.5        # EWMA weight of the newest error (recency)
_K_DOWN = 0.6       # multiplicative gain when latency is above setpoint
_K_UP = 0.2         # multiplicative gain when latency is below setpoint
_K_TREND = 0.3      # gain on the change of the smoothed error
_K_VOL = 0.05       # penalty per unit of error standard deviation
_VAR_BETA = 0.1     # EWMA weight of the variance estimate
_E_MIN = -1.0       # clamp on relative error
_E_MAX = 3.0
_G_MIN = -0.9       # clamp on the per-tick multiplicative change
_G_MAX = 0.25


def _f(x):
    return float(x)


class Controller:
    def __init__(self, config: dict):
        self.min_limit = _f(config.get("min_limit", 1.0))
        self.max_limit = _f(config.get("max_limit", 200.0))
        if self.max_limit < self.min_limit:
            self.max_limit = self.min_limit
        init = _f(config.get("initial_limit", 50.0))
        self.limit = min(self.max_limit, max(self.min_limit, init))

        self.setpoint = None     # last setpoint seen
        self.count = 0           # observations since last setpoint change
        self.s = 0.0             # smoothed relative error
        self.s_prev = 0.0        # previous smoothed error
        self.var = 0.0           # EWMA variance of error around smoothed value
        self.resets = 0          # number of setpoint changes seen
        self.saturated_ticks = 0

    # ------------------------------------------------------------------ core
    def _clamp_limit(self, x):
        if not math.isfinite(x):
            x = self.min_limit
        return min(self.max_limit, max(self.min_limit, x))

    def tick(self, obs: dict) -> dict:
        latency = _f(obs.get("latency_ms", 0.0))
        setpoint = _f(obs.get("setpoint_ms", 1.0))
        if not math.isfinite(setpoint) or setpoint <= 0.0:
            setpoint = 1e-9
        if not math.isfinite(latency):
            latency = setpoint * (1.0 + _E_MAX)

        # Setpoint change: discard all history-dependent correction state.
        setpoint_changed = 0
        if self.setpoint is None or setpoint != self.setpoint:
            if self.setpoint is not None:
                self.resets += 1
                setpoint_changed = 1
            self.setpoint = setpoint
            self.count = 0
            self.s = 0.0
            self.s_prev = 0.0
            self.var = 0.0

        e = (latency - setpoint) / setpoint
        e = min(_E_MAX, max(_E_MIN, e))

        # Smoothing with recency weighting.
        if self.count == 0:
            s_prev = e
            s = e
        else:
            s_prev = self.s
            s = _ALPHA * e + (1.0 - _ALPHA) * self.s
            dev = e - self.s
            self.var = (1.0 - _VAR_BETA) * self.var + _VAR_BETA * dev * dev
        trend = s - s_prev if self.count > 0 else 0.0
        std = math.sqrt(self.var) if self.var > 0.0 else 0.0

        # Asymmetric proportional term.
        if s > 0.0:
            g_prop = -_K_DOWN * s
        else:
            g_prop = -_K_UP * s
        g_trend = -_K_TREND * trend
        g_vol = -_K_VOL * std
        g = g_prop + g_trend + g_vol
        g = min(_G_MAX, max(_G_MIN, g))

        new_limit = self._clamp_limit(self.limit * (1.0 + g))

        # Anti-windup: while pinned at max_limit with latency at or below
        # setpoint, pin the smoothing state to the latest error so the next
        # response does not depend on how long saturation lasted.
        saturated = 1 if (new_limit >= self.max_limit and e <= 0.0) else 0
        if saturated:
            self.saturated_ticks += 1
            s = e
            s_prev = e
        else:
            self.saturated_ticks = 0

        self.s_prev = s_prev
        self.s = s
        self.count += 1
        self.limit = new_limit

        telemetry = {
            "error": e,
            "smoothed_error": s,
            "trend": trend,
            "stddev": std,
            "gain_prop": g_prop,
            "gain_trend": g_trend,
            "gain_vol": g_vol,
            "gain_total": g,
            "setpoint_ms": setpoint,
            "setpoint_changed": setpoint_changed,
            "setpoint_resets": self.resets,
            "samples_since_reset": self.count,
            "saturated": saturated,
            "saturated_ticks": self.saturated_ticks,
            "mode": "decrease" if g < 0.0 else ("increase" if g > 0.0 else "hold"),
            "limit": new_limit,
        }
        return {"limit": new_limit, "telemetry": telemetry}

    # --------------------------------------------------------- persistence
    def snapshot(self) -> str:
        state = {
            "v": _STATE_VERSION,
            "min_limit": self.min_limit,
            "max_limit": self.max_limit,
            "limit": self.limit,
            "setpoint": self.setpoint,
            "count": self.count,
            "s": self.s,
            "s_prev": self.s_prev,
            "var": self.var,
            "resets": self.resets,
            "saturated_ticks": self.saturated_ticks,
        }
        # Floats are hex-encoded so the round trip is exactly bit-identical.
        enc = {}
        for k, v in state.items():
            if isinstance(v, float):
                enc[k] = {"f": v.hex()}
            else:
                enc[k] = v
        return json.dumps(enc, sort_keys=True, separators=(",", ":"))

    @classmethod
    def restore(cls, blob: str) -> "Controller":
        raw = json.loads(blob)
        st = {}
        for k, v in raw.items():
            if isinstance(v, dict) and "f" in v:
                st[k] = float.fromhex(v["f"])
            else:
                st[k] = v
        obj = cls({
            "min_limit": st["min_limit"],
            "max_limit": st["max_limit"],
            "initial_limit": st["limit"],
        })
        obj.min_limit = st["min_limit"]
        obj.max_limit = st["max_limit"]
        obj.limit = st["limit"]
        obj.setpoint = st["setpoint"]
        obj.count = int(st["count"])
        obj.s = st["s"]
        obj.s_prev = st["s_prev"]
        obj.var = st["var"]
        obj.resets = int(st["resets"])
        obj.saturated_ticks = int(st["saturated_ticks"])
        return obj
