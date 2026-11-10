"""Adaptive concurrency controller.

Design
------
Each tick the controller computes the relative latency error

    e = clip((latency - setpoint) / setpoint, E_MIN, E_MAX)

and forms a correction signal

    x = e + trend + vol

* ``trend = clip(KD * (e - e_prev), -TREND_CAP*|e|, +TREND_CAP*|e|)`` makes
  recent movement count: a falling latency pushes the limit up harder than the
  same values seen while rising (trend recency).  Bounding it by ``|e|`` keeps
  it from ever overturning the asymmetry below.
* ``vol = KA * CV(arrivals)`` is a non-negative volatility penalty (EWMA
  coefficient of variation of offered load), so noisier workloads settle at a
  lower limit.

The limit is an integrator updated by an *additive* step

    limit -= G_DOWN * x   if x > 0      (latency too high: cut)
    limit -= G_UP   * x   otherwise     (latency low: grow)

with ``G_DOWN*(1-TREND_CAP) > 2*G_UP*(1+TREND_CAP)``, so the downward
correction to a deviation always strictly exceeds twice the upward correction
to the mirrored deviation.  Steps are additive (not proportional to the current
limit) so corrections are a function of observations only.

Filter state (previous error, arrival statistics) is reset when the setpoint
changes, and held reset while the limit is pinned at ``max_limit`` with
latency below setpoint, so the first response after saturation depends only on
the immediately observed error (anti-windup).

All state is plain floats/ints; snapshots are JSON (exact float repr).
"""

from __future__ import annotations

import json
import math

E_MIN = -1.0
E_MAX = 3.0
KD = 0.5          # derivative (trend) weight
TREND_CAP = 0.2   # trend term bounded to +-TREND_CAP*|e|
KA = 1.0        # volatility penalty weight on arrival coefficient of variation
VOL_ALPHA = 0.1   # EWMA factor for arrival statistics
G_UP = 3.0        # rps per unit of (negative) signal
G_DOWN = 10.0     # rps per unit of (positive) signal
STEP_UP_CAP = 4.0
STEP_DOWN_CAP = 40.0  # > 2 * STEP_UP_CAP


class Controller:
    _FIELDS = (
        "min_limit", "max_limit", "limit", "setpoint", "prev_e", "n",
        "arr_mean", "arr_sq", "arr_n", "saturated", "resets",
    )

    def __init__(self, config: dict):
        self.min_limit = float(config.get("min_limit", 1.0))
        self.max_limit = float(config.get("max_limit", 200.0))
        init = float(config.get("initial_limit", 50.0))
        self.limit = min(max(init, self.min_limit), self.max_limit)
        self.setpoint = None   # last seen setpoint
        self._reset_filters()
        self.saturated = 0
        self.resets = 0

    def _reset_filters(self) -> None:
        self.prev_e = 0.0
        self.n = 0          # observations since last reset
        self.arr_mean = 0.0
        self.arr_sq = 0.0
        self.arr_n = 0

    def tick(self, obs: dict) -> dict:
        lat = float(obs["latency_ms"])
        sp = float(obs["setpoint_ms"])
        arr = float(obs.get("arrival_rps", 0.0))
        if not math.isfinite(lat):
            lat = sp * (1.0 + E_MAX)

        sp_changed = 0
        if self.setpoint is None or sp != self.setpoint:
            if self.setpoint is not None:
                sp_changed = 1
                self.resets += 1
            self.setpoint = sp
            self._reset_filters()

        e = (lat - sp) / sp if sp > 0 else 0.0
        e = min(max(e, E_MIN), E_MAX)

        d = e - self.prev_e if self.n > 0 else 0.0
        tc = TREND_CAP * abs(e)
        trend = min(max(KD * d, -tc), tc)

        if math.isfinite(arr) and arr >= 0.0:
            if self.arr_n == 0:
                self.arr_mean = arr
                self.arr_sq = 0.0
            else:
                a = max(VOL_ALPHA, 1.0 / (self.arr_n + 1))
                diff = arr - self.arr_mean
                self.arr_mean += a * diff
                self.arr_sq = (1.0 - a) * (self.arr_sq + a * diff * diff)
            self.arr_n += 1
        cv = math.sqrt(self.arr_sq) / self.arr_mean if self.arr_mean > 1e-9 else 0.0
        vol = KA * cv

        x = e + trend + vol
        if x > 0.0:
            step = -min(G_DOWN * x, STEP_DOWN_CAP)
        else:
            step = min(-G_UP * x, STEP_UP_CAP)

        new = min(max(self.limit + step, self.min_limit), self.max_limit)
        self.limit = new
        self.prev_e = e
        self.n += 1

        # Anti-windup: while pinned at max_limit and still wanting more,
        # discard accumulated filter history.
        if new >= self.max_limit and x <= 0.0:
            self.saturated += 1
            self._reset_filters()
        else:
            self.saturated = 0

        telemetry = {
            "error": e,
            "trend": trend,
            "volatility": vol,
            "arrival_cv": cv,
            "signal": x,
            "step": step,
            "limit": new,
            "setpoint_ms": sp,
            "setpoint_changed": sp_changed,
            "saturated_ticks": self.saturated,
            "filter_n": self.n,
        }
        return {"limit": new, "telemetry": telemetry}

    def snapshot(self) -> str:
        return json.dumps({k: getattr(self, k) for k in self._FIELDS}, sort_keys=True)

    @classmethod
    def restore(cls, blob: str) -> "Controller":
        data = json.loads(blob)
        obj = cls.__new__(cls)
        for k in cls._FIELDS:
            setattr(obj, k, data[k])
        return obj
