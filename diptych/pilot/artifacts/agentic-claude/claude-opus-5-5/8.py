"""Adaptive concurrency controller.

Velocity-form (incremental) controller on relative latency error:

    e      = (latency - target) / setpoint
    target = setpoint * (1 - margin),  margin = KV * sd(e)   (volatility backoff)
    u      = clip(e + KD * trend, -CLIP, CLIP)               (trend = smoothed de/dt)
    limit += -K_DOWN * u   if u > 0   (back off)
             -K_UP   * u   otherwise  (probe up),   K_DOWN > 2 * K_UP

Design notes per requirement:
  1. Additive steps with K_DOWN = 3.25 * K_UP and a symmetric clip, so the
     downward step is always > 2x the upward step for equal |deviation|.
  2. The trend term is an exponentially weighted (recency-weighted) slope, so
     an improving run yields smaller u every tick than the worsening order.
  3. Velocity form has no integrator; additionally, while pinned at
     max_limit with latency under target all history (trend, variance) is
     reset, so the first excursion response is exactly f(immediate error).
  4. Margin grows with the EWMA standard deviation of the error, lowering the
     effective target -> lower converged limit under higher variance.
  5. On any setpoint change all history state is reset; corrections are
     additive (independent of the limit level) and use only new observations.
  6/7. Pure float arithmetic, no hashing/randomness; JSON snapshot with
     repr-exact floats.
  8. Fixed telemetry key set.
"""

from __future__ import annotations

import json
import math

K_UP = 2.0          # rps per unit relative error (upward)
K_DOWN = 6.5        # rps per unit relative error (downward), > 2 * K_UP
CLIP = 3.0          # symmetric clip on the control signal
KD = 0.5            # trend (derivative) weight
TREND_ALPHA = 0.5   # recency weight of newest slope sample
VAR_ALPHA = 0.1     # EWMA weight for mean/variance of error
KV = 1.0           # margin per unit error std-dev
MAX_MARGIN = 0.3

_VERSION = 1
_Q = 1048576.0      # 2**20: limits/deltas live on a dyadic grid so additions are exact


def _quant(x: float) -> float:
    return math.floor(x * _Q + 0.5) / _Q


class Controller:
    def __init__(self, config: dict):
        self.min_limit = float(config.get("min_limit", 1.0))
        self.max_limit = float(config.get("max_limit", 200.0))
        init = float(config.get("initial_limit", 50.0))
        self.limit = _quant(min(max(init, self.min_limit), self.max_limit))
        self.prev_sp = None
        self._reset_history()

    # ------------------------------------------------------------------ state
    def _reset_history(self) -> None:
        self.prev_e = None      # last raw (unmargined) relative error
        self.trend = 0.0        # EWMA of per-tick change in error
        self.n = 0              # samples in variance estimator
        self.mean_e = 0.0
        self.var_e = 0.0

    # ------------------------------------------------------------------- tick
    def tick(self, obs: dict) -> dict:
        lat = float(obs["latency_ms"])
        sp = float(obs["setpoint_ms"])
        if not (sp > 0.0) or math.isnan(sp):
            sp = 1.0
        if math.isnan(lat) or lat < 0.0:
            lat = 0.0

        sp_changed = 0
        if self.prev_sp is not None and sp != self.prev_sp:
            self._reset_history()
            sp_changed = 1
        self.prev_sp = sp

        raw_e = (lat - sp) / sp
        if raw_e > 1e6:
            raw_e = 1e6

        # Volatility margin from history *before* this observation.
        sd = math.sqrt(self.var_e) if self.var_e > 0.0 else 0.0
        margin = min(KV * sd, MAX_MARGIN)
        e = raw_e + margin  # (lat - sp*(1-margin)) / sp

        # Recency-weighted trend.
        if self.prev_e is None:
            trend = 0.0
        else:
            trend = (1.0 - TREND_ALPHA) * self.trend + TREND_ALPHA * (raw_e - self.prev_e)

        u = e + KD * trend
        if u > CLIP:
            u = CLIP
        elif u < -CLIP:
            u = -CLIP

        # Quantize outward on the way down, inward on the way up, so the
        # asymmetry ratio survives rounding.
        if u > 0.0:
            delta = -math.ceil(K_DOWN * u * _Q) / _Q
        else:
            delta = math.floor(-K_UP * u * _Q) / _Q
        new_limit = min(max(self.limit + delta, self.min_limit), self.max_limit)

        saturated = 1 if (new_limit >= self.max_limit and u <= 0.0) else 0
        if saturated:
            # Anti-windup: hold no history while pinned at the ceiling.
            self._reset_history()
        else:
            self.trend = trend
            self.prev_e = raw_e
            # Update variance estimator (EWMA, seeded by first sample).
            ce = min(raw_e, CLIP)
            if self.n == 0:
                self.mean_e = ce
                self.var_e = 0.0
            else:
                d = ce - self.mean_e
                self.mean_e += VAR_ALPHA * d
                self.var_e = (1.0 - VAR_ALPHA) * (self.var_e + VAR_ALPHA * d * d)
            self.n += 1

        self.limit = new_limit
        telemetry = {
            "limit": new_limit,
            "error": e,
            "raw_error": raw_e,
            "trend": trend,
            "signal": u,
            "delta": delta,
            "margin": margin,
            "err_sd": sd,
            "target_ms": sp * (1.0 - margin),
            "setpoint_ms": sp,
            "saturated": saturated,
            "setpoint_reset": sp_changed,
            "mode": "backoff" if u > 0.0 else "probe",
        }
        return {"limit": new_limit, "telemetry": telemetry}

    # -------------------------------------------------------------- snapshot
    def snapshot(self) -> str:
        return json.dumps({
            "v": _VERSION,
            "min_limit": self.min_limit,
            "max_limit": self.max_limit,
            "limit": self.limit,
            "prev_sp": self.prev_sp,
            "prev_e": self.prev_e,
            "trend": self.trend,
            "n": self.n,
            "mean_e": self.mean_e,
            "var_e": self.var_e,
        }, sort_keys=True)

    @classmethod
    def restore(cls, blob: str) -> "Controller":
        s = json.loads(blob)
        c = cls({"min_limit": s["min_limit"], "max_limit": s["max_limit"],
                 "initial_limit": s["limit"]})
        c.limit = s["limit"]
        c.prev_sp = s["prev_sp"]
        c.prev_e = s["prev_e"]
        c.trend = s["trend"]
        c.n = s["n"]
        c.mean_e = s["mean_e"]
        c.var_e = s["var_e"]
        return c
