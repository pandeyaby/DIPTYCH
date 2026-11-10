"""Adaptive concurrency controller.

Design
------
Each tick we observe a relative latency error

    e = (latency_ms - setpoint_ms) / setpoint_ms

and turn it into a signed, asymmetric proportional term ``p``: a positive
error (too slow) gets gain ``k_down``, a negative error (too fast) gets gain
``k_up``, with ``k_down > 2 * k_up`` (asymmetric-response requirement). ``e``
is clamped to +/-E_CAP first so one pathological outlier tick (e.g. latency
exploding near full utilization) can't produce an unbounded single-tick
correction.

The returned limit is the product of two independently-updated state
variables:

    limit_t = clip(base_t * (1 + trend_t))

* ``base`` is a slow, order-independent integrator (``base *= 1 + eps*p``
  each tick). Because it is a plain product of per-tick factors, its final
  value after a run does not depend on the order in which those factors were
  applied -- only on which observations occurred, not when. This is what
  lets the controller find the right long-run operating point (the thing
  that actually requires memory across hundreds of ticks) without that slow
  memory fighting the recency requirement below.
* ``trend`` is a fast EWMA of ``p`` (``trend = beta*p + (1-beta)*trend``),
  recomputed fresh against ``base`` every tick rather than compounded into
  it. Because an EWMA's own value is, by construction, more sensitive to
  recent samples than old ones, and because it is applied once (not
  re-applied every subsequent tick the way a compounded term would be),
  reordering a fixed set of observations so the good ones come last leaves
  the controller with a higher limit than reordering them so the bad ones
  come last -- i.e. recent observations dominate the outcome, satisfying the
  trend-recency requirement without being overwhelmed by ``base``'s slower,
  order-blind adaptation.

Anti-windup: while the reported limit sits at a bound and the current ``p``
would push further into that bound, both ``base`` and ``trend`` updates are
frozen. Each is then a convex/product combination of recent terms, bounded
by them, so neither keeps growing with the duration of saturation -- the
first response to a fresh excursion depends only on that excursion's own
error, not on how long the controller had been saturated.

Setpoint changes reset ``trend`` to zero (but not ``base``, which represents
the controller's current operating point, not a pending correction), so all
subsequent corrections are computed purely from post-change observations.
"""

from __future__ import annotations

import json

# k_down must strictly exceed 2 * k_up (asymmetric response requirement).
K_UP = 0.10
K_DOWN = 0.30
BETA = 0.50   # EWMA weight on the current tick's proportional term (trend).
EPS = 0.30    # per-tick step for the slow, order-independent base integrator.
E_CAP = 2.0   # clamp on relative error magnitude fed into the gains.

_STATE_KEYS = (
    "min_limit", "max_limit", "k_up", "k_down", "beta", "eps",
    "base", "trend", "limit", "last_setpoint",
)


class Controller:
    def __init__(self, config: dict) -> None:
        self.min_limit = float(config.get("min_limit", 1.0))
        self.max_limit = float(config.get("max_limit", 200.0))
        self.k_up = K_UP
        self.k_down = K_DOWN
        self.beta = BETA
        self.eps = EPS

        init = float(config.get("initial_limit", 50.0))
        init = min(self.max_limit, max(self.min_limit, init))
        self.base = init
        self.trend = 0.0
        self.limit = init
        self.last_setpoint = None  # None until the first tick is seen.

    def tick(self, obs: dict) -> dict:
        latency = float(obs["latency_ms"])
        setpoint = float(obs["setpoint_ms"])

        setpoint_changed = False
        if self.last_setpoint is not None and setpoint != self.last_setpoint:
            self.trend = 0.0
            setpoint_changed = True
        self.last_setpoint = setpoint

        e = (latency - setpoint) / setpoint
        e_clamped = max(-E_CAP, min(E_CAP, e))
        if e_clamped > 0.0:
            p = -self.k_down * e_clamped
        else:
            p = -self.k_up * e_clamped

        at_max = self.limit >= self.max_limit - 1e-9
        at_min = self.limit <= self.min_limit + 1e-9
        frozen = (at_max and p > 0.0) or (at_min and p < 0.0)

        if not frozen:
            self.trend = self.beta * p + (1.0 - self.beta) * self.trend
            self.base = self.base * (1.0 + self.eps * p)

        new_limit = self.base * (1.0 + self.trend)
        new_limit = min(self.max_limit, max(self.min_limit, new_limit))
        self.limit = new_limit

        telemetry = {
            "tick": int(obs["t"]),
            "limit": self.limit,
            "error": e,
            "p": p,
            "trend": self.trend,
            "saturated": 1 if (at_max or at_min) else 0,
            "setpoint_changed": 1 if setpoint_changed else 0,
        }

        return {"limit": self.limit, "telemetry": telemetry}

    def snapshot(self) -> str:
        state = {k: getattr(self, k) for k in _STATE_KEYS}
        return json.dumps(state, sort_keys=True)

    @classmethod
    def restore(cls, blob: str) -> "Controller":
        state = json.loads(blob)
        obj = cls.__new__(cls)
        for k in _STATE_KEYS:
            setattr(obj, k, state[k])
        return obj
