"""Adaptive concurrency controller.

Two pieces of state drive the limit:

``base_limit`` -- a persistent, slow-moving estimate of the right
concurrency level. It is updated every tick by a single *immediate*,
asymmetric proportional term:

    e = (latency - setpoint) / setpoint          # signed relative error
    primary = -KD * e   if e >= 0  (latency too high -> shrink)
            = -KI * e   if e <  0  (latency too low  -> grow)

KD > KI, so a latency excursion above setpoint shrinks the limit harder
than an equal-and-opposite excursion grows it. Because this update is a
product of per-tick multiplicative factors applied only to the *current*
error, it is exactly commutative under reordering of a fixed multiset of
observations -- the piece that must track the true long-run optimum has no
built-in recency bias of its own.

``ewma_err`` / ``ewma_var`` -- exponentially-weighted (recency-weighted)
estimates of the error level and its volatility, reset whenever
``setpoint_ms`` changes. Recency sensitivity (requirement: recent
observations must outweigh older ones) comes entirely from here, and
*only* from the freshest snapshot of these at the current tick -- they are
never folded back into ``base_limit``. This separation matters: a
per-tick EWMA-derived correction that gets compounded into a persistent
accumulator every tick ends up giving *more* total weight to early
observations (each old sample keeps nudging the accumulator at every
subsequent tick) than to late ones, which is backwards from what "recent
observations carry more weight" requires. Recomputing the trim fresh each
tick from the live EWMA, instead of accumulating it, avoids that trap:
the final decision is governed by the most recent trend snapshot, which
is exactly what recency weighting should mean.

The reported limit is:

    trend = -KT * ewma_err          (symmetric: same gain either sign)
    vol   = -KV * ewma_var          (always <= 0: noisier -> more cautious)
    trim  = clip(trend + vol, -TRIM_CAP, TRIM_CAP)
    limit = clip(base_limit * (1 + trim), min_limit, max_limit)

Anti-windup falls out of using only *bounded, convergent* state (EWMA
convex combinations, never an unbounded running sum) everywhere: after
sustained saturation, ewma_err/ewma_var have converged to a fixed value
regardless of how long saturation lasted, so the response to a fresh
excursion depends only on that excursion's own error, not on saturation
duration.
"""

from __future__ import annotations

import json


class Controller:
    # asymmetric response gains for the persistent base-limit update
    _KD = 0.50
    _KI = 0.08
    # max fractional change to base_limit from the primary term in one tick
    _PRIMARY_STEP = 0.25
    # symmetric recency-weighted trend gain, and volatility suppression gain
    _KT = 0.10
    _KV = 0.30
    # max fractional trim (trend + volatility) applied on top of base_limit
    _TRIM_CAP = 0.40
    # EWMA decay rates
    _ALPHA = 0.30
    _ALPHA_V = 0.20
    # relative error is clipped to this magnitude before use, so a single
    # pathological latency spike cannot dominate the trend/volatility state
    _ERR_CLIP = 1.5
    # the recency-weighted variance estimate is capped, bounding how much a
    # transient outlier can suppress the limit afterward
    _VAR_CAP = 1.0

    def __init__(self, config: dict):
        self.min_limit = float(config["min_limit"])
        self.max_limit = float(config["max_limit"])
        initial = float(config.get("initial_limit", (self.min_limit + self.max_limit) / 2.0))
        self._base_limit = min(max(initial, self.min_limit), self.max_limit)

        self._ewma_err = 0.0
        self._has_ewma = False
        self._ewma_var = 0.0
        self._last_setpoint = None
        self._tick_count = 0

    def tick(self, obs: dict) -> dict:
        latency = float(obs["latency_ms"])
        setpoint = float(obs["setpoint_ms"])
        t = obs.get("t", self._tick_count)

        setpoint_changed = 0
        if self._last_setpoint is not None and setpoint != self._last_setpoint:
            setpoint_changed = 1
            self._ewma_err = 0.0
            self._has_ewma = False
            self._ewma_var = 0.0
        self._last_setpoint = setpoint

        e = (latency - setpoint) / setpoint
        e_c = e
        if e_c > self._ERR_CLIP:
            e_c = self._ERR_CLIP
        elif e_c < -self._ERR_CLIP:
            e_c = -self._ERR_CLIP

        if e_c >= 0.0:
            primary = -self._KD * e_c
        else:
            primary = -self._KI * e_c
        if primary > self._PRIMARY_STEP:
            primary = self._PRIMARY_STEP
        elif primary < -self._PRIMARY_STEP:
            primary = -self._PRIMARY_STEP

        base_limit = self._base_limit * (1.0 + primary)
        if base_limit < self.min_limit:
            base_limit = self.min_limit
        elif base_limit > self.max_limit:
            base_limit = self.max_limit

        prev_ewma = self._ewma_err if self._has_ewma else e_c
        new_ewma = self._ALPHA * e_c + (1.0 - self._ALPHA) * prev_ewma
        dev = e_c - new_ewma
        new_var = self._ALPHA_V * (dev * dev) + (1.0 - self._ALPHA_V) * self._ewma_var
        if new_var > self._VAR_CAP:
            new_var = self._VAR_CAP

        trend = -self._KT * new_ewma
        vol = -self._KV * new_var
        trim = trend + vol
        if trim > self._TRIM_CAP:
            trim = self._TRIM_CAP
        elif trim < -self._TRIM_CAP:
            trim = -self._TRIM_CAP

        new_limit = base_limit * (1.0 + trim)
        if new_limit < self.min_limit:
            new_limit = self.min_limit
        elif new_limit > self.max_limit:
            new_limit = self.max_limit

        saturated = 1 if (new_limit <= self.min_limit or new_limit >= self.max_limit) else 0

        self._base_limit = base_limit
        self._ewma_err = new_ewma
        self._has_ewma = True
        self._ewma_var = new_var
        self._tick_count = int(t) + 1

        telemetry = {
            "error": e,
            "base_limit": base_limit,
            "ewma_error": new_ewma,
            "ewma_var": new_var,
            "primary_adj": primary,
            "trend_adj": trend,
            "vol_adj": vol,
            "trim": trim,
            "setpoint_changed": setpoint_changed,
            "saturated": saturated,
            "tick": int(t),
        }
        return {"limit": new_limit, "telemetry": telemetry}

    def snapshot(self) -> str:
        state = {
            "min_limit": self.min_limit,
            "max_limit": self.max_limit,
            "base_limit": self._base_limit,
            "ewma_err": self._ewma_err,
            "has_ewma": self._has_ewma,
            "ewma_var": self._ewma_var,
            "last_setpoint": self._last_setpoint,
            "tick_count": self._tick_count,
        }
        return json.dumps(state, sort_keys=True)

    @classmethod
    def restore(cls, blob: str) -> "Controller":
        state = json.loads(blob)
        obj = cls({
            "min_limit": state["min_limit"],
            "max_limit": state["max_limit"],
            "initial_limit": state["base_limit"],
        })
        obj._base_limit = float(state["base_limit"])
        obj._ewma_err = float(state["ewma_err"])
        obj._has_ewma = bool(state["has_ewma"])
        obj._ewma_var = float(state["ewma_var"])
        obj._last_setpoint = state["last_setpoint"]
        obj._tick_count = int(state["tick_count"])
        return obj
