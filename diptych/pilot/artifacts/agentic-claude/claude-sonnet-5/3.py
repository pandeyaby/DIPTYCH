"""Adaptive concurrency controller.

Velocity-form (incremental) controller on the normalised latency error
e = (latency - setpoint) / setpoint.  Because the limit itself is the only
integrator and is clamped every tick, there is no separate integral state that
can wind up.

Per tick:
    s     = e + TREND * (e - m)            m = EWMA of past errors (recency)
    s     clipped to +/-(1 + TREND) * |e|  (first response bound by current error)
    delta = -g(s) - K_UP * VOL * sd        g(x) = K_DOWN*x if x > 0 else K_UP*x
    limit = clamp(limit + delta)

K_DOWN > 2 * K_UP gives the asymmetric response; the sd term (EWMA of the
error's standard deviation) pushes the limit down under volatility.  A setpoint
change clears the error history so later corrections use only post-change
observations.
"""

from __future__ import annotations

import json
import math

K_UP = 2.0
K_DOWN = 6.0          # strictly more than 2 * K_UP
TREND = 1.0
ALPHA_M = 0.3         # weight of the newest error in the trend EWMA
ALPHA_V = 0.1         # weight of the newest squared deviation in the variance EWMA
VOL = 1.0
E_MAX = 5.0           # cap on the normalised error so a spike cannot slam the limit


class Controller:
    def __init__(self, config: dict):
        self.min_limit = float(config.get("min_limit", 1.0))
        self.max_limit = float(config.get("max_limit", 200.0))
        init = float(config.get("initial_limit", 50.0))
        self.limit = min(max(init, self.min_limit), self.max_limit)
        self.setpoint = None
        self.m = 0.0
        self.var = 0.0
        self.n = 0

    def tick(self, obs: dict) -> dict:
        sp = float(obs["setpoint_ms"])
        lat = float(obs["latency_ms"])
        if self.setpoint is None or sp != self.setpoint:
            self.setpoint = sp
            self.m = 0.0
            self.var = 0.0
            self.n = 0

        delta = 0.0
        e = 0.0
        s = 0.0
        if math.isfinite(lat) and math.isfinite(sp) and sp > 0.0:
            e = min(max((lat - sp) / sp, -1.0), E_MAX)
            s = e + TREND * (e - self.m)
            bound = (1.0 + TREND) * abs(e)
            s = min(max(s, -bound), bound)
            d = e - self.m
            self.var = (1.0 - ALPHA_V) * self.var + ALPHA_V * d * d
            self.m = (1.0 - ALPHA_M) * self.m + ALPHA_M * e
            self.n += 1
            sd = math.sqrt(self.var)
            gs = K_DOWN * s if s > 0.0 else K_UP * s
            delta = -gs - K_UP * VOL * sd

        self.limit = min(max(self.limit + delta, self.min_limit), self.max_limit)
        return {
            "limit": self.limit,
            "telemetry": {
                "error": e,
                "signal": s,
                "trend_mean": self.m,
                "volatility": math.sqrt(self.var),
                "delta": delta,
                "samples": self.n,
                "saturated": "max" if self.limit >= self.max_limit
                else ("min" if self.limit <= self.min_limit else "none"),
            },
        }

    def snapshot(self) -> str:
        return json.dumps({
            "min_limit": self.min_limit,
            "max_limit": self.max_limit,
            "limit": self.limit,
            "setpoint": self.setpoint,
            "m": self.m,
            "var": self.var,
            "n": self.n,
        })

    @classmethod
    def restore(cls, blob: str) -> "Controller":
        d = json.loads(blob)
        c = cls({"min_limit": d["min_limit"], "max_limit": d["max_limit"],
                 "initial_limit": d["limit"]})
        c.limit = d["limit"]
        c.setpoint = d["setpoint"]
        c.m = d["m"]
        c.var = d["var"]
        c.n = d["n"]
        return c
