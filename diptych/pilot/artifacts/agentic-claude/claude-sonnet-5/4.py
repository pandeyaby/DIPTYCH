"""Adaptive concurrency controller.

Structure (all error terms are normalised by the setpoint, e = (sp - lat) / sp,
so e > 0 means there is headroom and e < 0 means latency is too high):

* ``base`` is an integral state, clamped to ``[min_limit, max_limit]`` on every
  update (clamping the state, not just the output, is the anti-windup: the
  first correction after saturation starts from the bound and is a function of
  the current error only).
* The integral step is asymmetric: a negative effective error is scaled by
  ``K_DOWN`` and a positive one by ``K_UP`` (K_DOWN = 3 * K_UP).
* The effective error is ``e - K_VOL * sigma`` where ``sigma`` is an
  exponentially weighted standard deviation of ``e``; noisy workloads are thus
  steered to a latency further below the setpoint, i.e. a lower limit.
* A derivative-like trend term, ``K_TREND * (fast_ewma - slow_ewma)`` of ``e``,
  is added to the output. Its exponential weights make recent observations
  count more than old ones.
* Everything derived from history (EWMAs, variance) is reset when the setpoint
  changes, so post-change corrections depend only on post-change observations.
"""

from __future__ import annotations

import json
import math

K_UP = 4.0
K_DOWN = 3.0 * K_UP
K_VOL = 1.5
K_TREND = 20.0
A_FAST = 0.5
A_SLOW = 0.1
A_VOL = 0.1
E_MIN = -2.0
E_MAX = 2.0
E_VOL = 0.5      # |e| clip used only for the variance estimate
SIGMA_MAX = 0.25

_STATE_KEYS = ("min_limit", "max_limit", "base", "setpoint", "fast", "slow",
               "mean", "var", "n", "resets", "primed")


class Controller:
    def __init__(self, config: dict):
        self.min_limit = float(config.get("min_limit", 1.0))
        self.max_limit = float(config.get("max_limit", 200.0))
        init = float(config.get("initial_limit", 50.0))
        self.base = self._clamp(init)
        self.setpoint = 0.0
        self.fast = 0.0
        self.slow = 0.0
        self.mean = 0.0
        self.var = 0.0
        self.n = 0          # observations since the last (re)start of history
        self.resets = 0
        self.primed = False  # has any setpoint been seen yet

    def _clamp(self, x: float) -> float:
        return min(max(x, self.min_limit), self.max_limit)

    def tick(self, obs: dict) -> dict:
        sp = float(obs["setpoint_ms"])
        lat = float(obs["latency_ms"])
        if not math.isfinite(sp) or sp <= 0.0:
            sp = self.setpoint if self.setpoint > 0.0 else 1.0
        if not math.isfinite(lat):
            lat = sp  # unusable sample: treat as on-target

        if not self.primed or sp != self.setpoint:
            if self.primed:
                self.resets += 1
            self.primed = True
            self.setpoint = sp
            self.n = 0
            self.fast = self.slow = self.mean = self.var = 0.0

        e = (sp - lat) / sp
        e = min(max(e, E_MIN), E_MAX)

        ev = min(max(e, -E_VOL), E_VOL)
        if self.n == 0:
            self.fast = self.slow = e
            self.mean = ev
            self.var = 0.0
        else:
            self.fast += A_FAST * (e - self.fast)
            self.slow += A_SLOW * (e - self.slow)
            d = ev - self.mean
            self.mean += A_VOL * d
            self.var = (1.0 - A_VOL) * (self.var + A_VOL * d * d)
        self.n += 1

        sigma = min(math.sqrt(self.var), SIGMA_MAX)
        e_eff = e - K_VOL * sigma
        step = (K_UP if e_eff > 0.0 else K_DOWN) * e_eff
        self.base = self._clamp(self.base + step)

        trend = self.fast - self.slow
        limit = self._clamp(self.base + K_TREND * trend)

        return {
            "limit": limit,
            "telemetry": {
                "base": self.base,
                "error": e,
                "error_eff": e_eff,
                "sigma": sigma,
                "trend": trend,
                "ewma_fast": self.fast,
                "ewma_slow": self.slow,
                "setpoint": self.setpoint,
                "step": step,
                "history_n": self.n,
                "setpoint_changes": self.resets,
            },
        }

    def snapshot(self) -> str:
        return json.dumps({k: getattr(self, k) for k in _STATE_KEYS}, sort_keys=True)

    @classmethod
    def restore(cls, blob: str) -> "Controller":
        d = json.loads(blob)
        c = cls({"min_limit": d["min_limit"], "max_limit": d["max_limit"],
                 "initial_limit": d["base"]})
        for k in _STATE_KEYS:
            setattr(c, k, d[k])
        return c
