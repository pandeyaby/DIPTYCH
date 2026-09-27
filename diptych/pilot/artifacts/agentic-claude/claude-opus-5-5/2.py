"""Adaptive concurrency controller.

Integral controller on the normalized latency error
``e = (setpoint - latency) / setpoint``. Each tick the limit moves by an
additive correction

    correction = K(sign e) * w(sign e) * e - MARGIN_GAIN * margin
                 - DROP_GAIN * sqrt(max(-de, 0)),     de = e - e_prev

with:

* asymmetric gains: ``K_DOWN = 3 * K_UP``, and the trend weights ``w`` stay in
  ``[1 - TREND_W, 1 + TREND_W]``, so the down/up ratio never drops below
  ``3 * 0.85 / 1.15 > 2`` whatever the history. The margin and the worsening
  penalty only ever push downward, which can only widen that ratio;
* trend recency: ``w`` follows the direction of the latest error change, so an
  observation that improves on the previous one has its upward correction
  amplified (downward one damped), and a worsening one the reverse. The
  worsening penalty adds a downward push on every step that gets worse; being
  concave, a run of small worsening steps costs more than one jump of the same
  total. The same observations replayed in improving order therefore end
  higher than in worsening order;
* volatility suppression: ``margin`` is the coefficient of variation of the
  offered load (EWMA). Its constant downward push must be balanced by latency
  running below setpoint, so a noisier workload settles at a strictly lower
  limit. The push depends only on the load series, not on latency order;
* anti-windup by conditional integration: while pinned at a bound and pushed
  further into it, all history (trend reference, volatility) is cleared, so the
  first correction after saturation depends only on the current observation;
* setpoint-change quiet: the same history reset happens when the setpoint
  changes, so later corrections depend only on post-change observations.

Corrections are additive and quantized to a binary grid, so the sequence of
limit changes is bit-identical whatever limit level earlier history produced
(apart from clamping at the bounds). State is plain floats/ints serialized
with ``json`` (floats round-trip exactly).
"""

from __future__ import annotations

import json
import math

K_UP = 4.0            # rps per unit of normalized error, latency below setpoint
K_DOWN = 12.0         # rps per unit of normalized error, latency above setpoint
TREND_SCALE = 0.02    # error change mapped to a full-scale trend of +-1
TREND_W = 0.15        # max relative gain modulation by the trend
DROP_GAIN = 0.5       # extra downward correction per sqrt(error worsening)
VOL_ALPHA = 0.05      # EWMA weight for offered-load mean / variance
VOL_GAIN = 1.0        # margin per unit of load CV
VOL_MARGIN_MAX = 0.3  # cap on the margin
MARGIN_GAIN = 6.0     # rps of downward correction per unit of margin
VOL_CLIP = 1.0        # per-sample relative load deviation clip
E_MIN, E_MAX = -2.0, 1.0
GRID = 2.0 ** -30     # limits and corrections live on this grid so sums are exact

_STATE_KEYS = ("min_limit", "max_limit", "limit", "setpoint", "e_prev",
               "load_mean", "load_var", "load_n", "since_change", "saturated")


def _q(x: float) -> float:
    return round(x / GRID) * GRID


def _clip(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


class Controller:
    def __init__(self, config: dict):
        self.min_limit = float(config.get("min_limit", 1.0))
        self.max_limit = float(config.get("max_limit", 200.0))
        self.limit = _clip(_q(float(config.get("initial_limit", 50.0))), self.min_limit, self.max_limit)
        self.setpoint = None
        self.since_change = 0
        self.saturated = 0
        self._reset_history()

    def _reset_history(self) -> None:
        self.e_prev = None
        self.load_mean = 0.0
        self.load_var = 0.0
        self.load_n = 0

    def tick(self, obs: dict) -> dict:
        lat = float(obs["latency_ms"])
        sp = float(obs["setpoint_ms"])
        load = max(float(obs.get("arrival_rps", 0.0)), 0.0)
        if not sp > 0.0:
            sp = 1e-9

        setpoint_changed = self.setpoint is not None and sp != self.setpoint
        if setpoint_changed:
            self._reset_history()
            self.since_change = 0
        self.setpoint = sp

        e = _clip((sp - lat) / sp, E_MIN, E_MAX)

        # Volatility margin from the offered load seen before this tick.
        if self.load_n >= 2 and self.load_mean > 0.0:
            cv = math.sqrt(self.load_var) / self.load_mean
        else:
            cv = 0.0
        margin = min(VOL_GAIN * cv, VOL_MARGIN_MAX)

        # Trend: direction of the latest error change (recent weighs most).
        de = 0.0 if self.e_prev is None else e - self.e_prev
        trend = _clip(de / TREND_SCALE, -1.0, 1.0)

        if e > 0.0:
            gain = K_UP * (1.0 + TREND_W * trend)
        else:
            gain = K_DOWN * (1.0 - TREND_W * trend)
        penalty = DROP_GAIN * math.sqrt(max(-de, 0.0))
        push = MARGIN_GAIN * margin
        correction = _q(gain * e - push - penalty)

        new = self.limit + correction
        pinned = (new >= self.max_limit and correction > 0.0) or (new <= self.min_limit and correction < 0.0)
        new = _clip(new, self.min_limit, self.max_limit)

        if pinned:
            # Conditional integration: pinned against a bound, history carries no
            # information about how to respond once the excursion ends.
            self._reset_history()
            self.saturated = 1
        else:
            self.saturated = 0
            self.e_prev = e
            # EWMA mean / variance of offered load (warm start over first samples).
            self.load_n += 1
            a = max(VOL_ALPHA, 1.0 / self.load_n)
            if self.load_n == 1:
                self.load_mean = load
            else:
                ref = max(self.load_mean, 1e-9)
                d = _clip(load - self.load_mean, -VOL_CLIP * ref, VOL_CLIP * ref)
                self.load_mean += a * d
                self.load_var = (1.0 - a) * (self.load_var + a * d * d)

        self.limit = new
        self.since_change += 1

        telemetry = {
            "error": e,
            "correction": correction,
            "gain": gain,
            "trend": trend,
            "worsening_penalty": penalty,
            "load_cv": cv,
            "margin": margin,
            "margin_push": push,
            "saturated": self.saturated,
            "setpoint_changed": 1 if setpoint_changed else 0,
            "ticks_since_setpoint_change": self.since_change,
            "limit": new,
        }
        return {"limit": new, "telemetry": telemetry}

    def snapshot(self) -> str:
        return json.dumps({k: getattr(self, k) for k in _STATE_KEYS}, sort_keys=True)

    @classmethod
    def restore(cls, blob: str) -> "Controller":
        state = json.loads(blob)
        obj = cls.__new__(cls)
        for k in _STATE_KEYS:
            setattr(obj, k, state[k])
        return obj
