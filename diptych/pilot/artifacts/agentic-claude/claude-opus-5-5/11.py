"""Adaptive concurrency controller.

Velocity-form PI controller on the normalised latency error
``e = (setpoint - latency) / setpoint`` (positive = headroom, raise limit).

* Integral part: ``KI * e`` every tick (order independent).
* Proportional part on a recency-weighted EWMA ``s`` of the error, in velocity
  form ``KP * (s_t - s_{t-1})``; it telescopes to ``KP * s_N``, so recent
  observations dominate the final limit (trend recency).
* The combined raw step is scaled asymmetrically: downward steps use
  ``DOWN_GAIN`` (> 2x) the upward gain.  Upward steps are additionally damped by
  the observed error volatility (volatility suppression).
* Anti-windup: whenever the output is clamped at a bound, the filter state is
  reset so nothing accumulates during saturation; the first response after an
  excursion depends only on the immediately observed error.
* A setpoint change resets every piece of error history (filter, volatility).

Pure standard library, no randomness, no clocks: deterministic.
"""

from __future__ import annotations

import json

KI = 6.0          # integral gain (rps per unit normalised error)
KP = 10.0        # proportional gain on the EWMA error (velocity form)
ALPHA = 0.3       # EWMA weight of the newest error sample
VOL_ALPHA = 0.1   # EWMA weight for the error variance estimate
UP_GAIN = 1.0     # scale applied to upward steps
DOWN_GAIN = 3.0   # scale applied to downward steps (> 2 * UP_GAIN)
VOL_K = 10.0     # upward damping per unit error std-dev
MAX_STEP = 25.0   # absolute cap on one tick's step (rps)

_TELEMETRY_KEYS = (
    "error", "error_ewma", "error_var", "error_std", "raw_step", "step",
    "up_gain", "saturated", "setpoint_ms", "samples", "reset",
)


class Controller:
    def __init__(self, config: dict):
        self.min_limit = float(config.get("min_limit", 1.0))
        self.max_limit = float(config.get("max_limit", 200.0))
        init = float(config.get("initial_limit", 50.0))
        self.limit = min(max(init, self.min_limit), self.max_limit)
        self.s = 0.0            # EWMA of error since last reset
        self.mean = 0.0         # EWMA mean for variance estimate
        self.var = 0.0          # EWMA variance of error
        self.n = 0              # samples since last reset
        self.setpoint = None    # last seen setpoint

    # ------------------------------------------------------------------ core
    def _reset_history(self) -> None:
        self.s = 0.0
        self.mean = 0.0
        self.var = 0.0
        self.n = 0

    def tick(self, obs: dict) -> dict:
        sp = float(obs["setpoint_ms"])
        lat = float(obs["latency_ms"])
        reset = 0
        if self.setpoint is None or sp != self.setpoint:
            if self.setpoint is not None:
                reset = 1
            self._reset_history()
            self.setpoint = sp

        e = (sp - lat) / sp if sp > 0.0 else 0.0
        if e != e:  # NaN guard
            e = 0.0
        e = min(max(e, -5.0), 5.0)

        # Volatility estimate (uses observations before this one, so the very
        # first sample after a reset is undamped).
        std = self.var ** 0.5
        if self.n == 0:
            self.mean = e
            self.var = 0.0
        else:
            d = e - self.mean
            self.mean += VOL_ALPHA * d
            self.var = (1.0 - VOL_ALPHA) * (self.var + VOL_ALPHA * d * d)

        s_prev = self.s
        self.s = s_prev + ALPHA * (e - s_prev)
        self.n += 1

        # Volatility backoff: headroom smaller than VOL_B * std is not
        # treated as headroom (more variance -> lower operating point).
        if e > 0.0:
            e_eff = max(e - VOL_B * std, 0.0)
        else:
            e_eff = e
        raw = KI * e_eff + KP * (self.s - s_prev)
        up_gain = UP_GAIN / (1.0 + VOL_K * std)
        # Sign-consistent: headroom never lowers the limit, excess latency
        # never raises it.
        if e >= 0.0:
            step = up_gain * max(raw, 0.0)
        else:
            step = DOWN_GAIN * min(raw, 0.0)
        step = min(max(step, -MAX_STEP * DOWN_GAIN), MAX_STEP * up_gain)

        new = self.limit + step
        saturated = 0
        if new >= self.max_limit:
            new = self.max_limit
            saturated = 1
        elif new <= self.min_limit:
            new = self.min_limit
            saturated = -1
        if saturated:
            # Anti-windup: do not carry filter state accumulated while pinned.
            self.s = 0.0
        self.limit = new

        telemetry = {
            "error": e,
            "error_ewma": self.s,
            "error_var": self.var,
            "error_std": std,
            "raw_step": raw,
            "step": step,
            "up_gain": up_gain,
            "saturated": saturated,
            "setpoint_ms": sp,
            "samples": self.n,
            "reset": reset,
        }
        return {"limit": float(new), "telemetry": telemetry}

    # ------------------------------------------------------------ persistence
    def snapshot(self) -> str:
        return json.dumps({
            "min_limit": self.min_limit, "max_limit": self.max_limit,
            "limit": self.limit, "s": self.s, "mean": self.mean,
            "var": self.var, "n": self.n, "setpoint": self.setpoint,
        }, sort_keys=True)

    @classmethod
    def restore(cls, blob: str) -> "Controller":
        d = json.loads(blob)
        c = cls({"min_limit": d["min_limit"], "max_limit": d["max_limit"],
                 "initial_limit": d["limit"]})
        c.limit = d["limit"]
        c.s = d["s"]
        c.mean = d["mean"]
        c.var = d["var"]
        c.n = d["n"]
        c.setpoint = d["setpoint"]
        return c
