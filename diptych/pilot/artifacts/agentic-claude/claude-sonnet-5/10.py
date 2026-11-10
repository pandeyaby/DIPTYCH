"""Adaptive concurrency controller.

Regulates a concurrency limit so observed latency tracks a setpoint, using
an asymmetric proportional + recency-weighted-trend (EWMA) control law:

  - "down" corrections (latency above setpoint) use a much larger gain than
    "up" corrections (latency below setpoint), so the limiter sheds load
    fast and grows back slowly.
  - the trend term is an exponentially-weighted moving average of the
    normalized error, which inherently bounds itself (it is a convex
    combination of past errors, not an accumulating sum), so it cannot
    "wind up" without limit while saturated, and it weights recent
    observations more heavily than old ones.
  - the trend term's integration is frozen whenever the limit is pinned at
    a bound and the current error points further into that bound, which is
    the standard anti-windup technique (conditional integration).
  - the trend term is reset on a setpoint change so stale pre-change error
    history cannot influence post-change decisions.
"""

from __future__ import annotations

import json

# Control gains. KD (downward/shrink gain) strictly exceeds 2x KU (upward/
# grow gain), which is what gives the asymmetric response.
KD = 0.4
KU = 0.15
# Weight split between the instantaneous error and the recency-weighted
# trend (EWMA) of the error.
WP = 0.5
WI = 0.5
# EWMA decay for the trend term: higher ALPHA weights recent ticks more.
ALPHA = 0.25
# Clip normalized error before use, so a pathological latency spike cannot
# produce an unbounded single-tick correction.
ERR_CLIP = 1.0
# Tolerance for "is the limit pinned at a bound" checks.
EPS = 1e-9


def _asym(x: float) -> float:
    """Asymmetric gain: shrinking (x >= 0) is weighted more than growing."""
    return -KD * x if x >= 0.0 else -KU * x


class Controller:
    def __init__(self, config: dict):
        self.min_limit = float(config["min_limit"])
        self.max_limit = float(config["max_limit"])
        self.limit = float(config["initial_limit"])
        self.ewma = 0.0
        self.last_setpoint = None

    def tick(self, obs: dict) -> dict:
        setpoint = float(obs["setpoint_ms"])
        latency = float(obs["latency_ms"])
        prev_limit = self.limit

        raw_error = (latency - setpoint) / setpoint
        error = max(-ERR_CLIP, min(ERR_CLIP, raw_error))

        setpoint_changed = self.last_setpoint is None or setpoint != self.last_setpoint
        self.last_setpoint = setpoint

        frozen = 0
        if setpoint_changed:
            # Discard history from before the setpoint change: corrections
            # from here on must depend only on post-change observations.
            self.ewma = error
        else:
            at_max = prev_limit >= self.max_limit - EPS
            at_min = prev_limit <= self.min_limit + EPS
            pushing_further_into_max = at_max and error <= 0.0
            pushing_further_into_min = at_min and error >= 0.0
            if pushing_further_into_max or pushing_further_into_min:
                # Anti-windup: don't keep integrating while saturated and
                # the error still points further into the saturated bound.
                frozen = 1
            else:
                self.ewma = ALPHA * error + (1.0 - ALPHA) * self.ewma

        p_component = WP * error
        i_component = WI * self.ewma
        correction = _asym(p_component) + _asym(i_component)

        raw_new_limit = prev_limit * (1.0 + correction)
        new_limit = max(self.min_limit, min(self.max_limit, raw_new_limit))
        saturated = 1 if new_limit != raw_new_limit else 0

        self.limit = new_limit

        telemetry = {
            "t": int(obs["t"]),
            "raw_error": raw_error,
            "error": error,
            "ewma": self.ewma,
            "correction": correction,
            "raw_limit": raw_new_limit,
            "saturated": saturated,
            "frozen": frozen,
        }
        return {"limit": new_limit, "telemetry": telemetry}

    def snapshot(self) -> str:
        return json.dumps({
            "min_limit": self.min_limit,
            "max_limit": self.max_limit,
            "limit": self.limit,
            "ewma": self.ewma,
            "last_setpoint": self.last_setpoint,
        })

    @classmethod
    def restore(cls, blob: str) -> "Controller":
        state = json.loads(blob)
        obj = cls.__new__(cls)
        obj.min_limit = float(state["min_limit"])
        obj.max_limit = float(state["max_limit"])
        obj.limit = float(state["limit"])
        obj.ewma = float(state["ewma"])
        obj.last_setpoint = state["last_setpoint"]
        return obj
