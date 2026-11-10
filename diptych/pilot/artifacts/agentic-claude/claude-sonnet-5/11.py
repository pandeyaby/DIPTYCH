"""Adaptive concurrency controller.

Regulates a concurrency limit so observed latency tracks a setpoint.

Design notes
------------
The controller keeps a single scalar piece of state across ticks: an
exponentially-weighted moving average (EWMA) of the relative latency error

    e_t = (latency_ms - setpoint_ms) / setpoint_ms

Recency weighting (requirement 2) falls directly out of the EWMA recursion:
each new sample is blended in with weight ``alpha``, so older samples decay
geometrically and can never dominate a long enough recent run.

Because the EWMA is a weighted *average* of past errors, it is always
bounded between the smallest and largest error seen recently -- it cannot
grow without bound while the limit is pinned at a rail the way a classic
integral term would. That is what gives anti-windup (requirement 3) for
free: once the EWMA has settled near its steady value during a sustained
saturation period, one more tick of saturation does not change that value,
so the very next excursion is answered using (mostly) the fresh sample, not
an ever-growing backlog.

Asymmetry (requirement 1) is applied at the very last step, when the EWMA is
turned into a multiplicative change to the limit: a positive EWMA (latency
running hot) is scaled by ``K_DOWN``, a negative EWMA (latency running cool)
by the strictly smaller ``K_UP``. Because the scaling is linear and applied
after the (symmetric) EWMA smoothing, the ratio between a downward and an
upward correction of equal magnitude is exactly ``K_DOWN / K_UP`` regardless
of history, which is what makes requirement 1 hold as a clean, checkable
invariant and not just an emergent property of the trace.

That same asymmetry is also what produces volatility suppression
(requirement 4): under higher-variance load, latency occasionally spikes
well past the setpoint even when its mean does not change, and those spikes
are punished more than an equal-sized dip is rewarded, so the time-averaged
pressure on the limit is net negative as variance grows.

A setpoint change (requirement 5) invalidates the EWMA, since it was
computed against the old setpoint. The controller detects the change and
reseeds the EWMA from the new tick's error alone, so corrections depend only
on post-change observations.
"""

from __future__ import annotations

import json

ALPHA = 0.30      # EWMA smoothing weight on the newest sample.
K_DOWN = 0.90     # Gain applied when the EWMA error is positive (too slow).
K_UP = 0.35       # Gain applied when the EWMA error is negative (too fast).
# Requirement 1: K_DOWN must strictly exceed 2 * K_UP.
assert K_DOWN > 2.0 * K_UP


class Controller:
    def __init__(self, config: dict):
        self.min_limit = float(config["min_limit"])
        self.max_limit = float(config["max_limit"])
        self.limit = float(config["initial_limit"])
        self.ewma_error = 0.0
        self.last_setpoint: float | None = None
        self._initialized = False

    def tick(self, obs: dict) -> dict:
        setpoint = float(obs["setpoint_ms"])
        latency = float(obs["latency_ms"])
        error = (latency - setpoint) / setpoint

        setpoint_changed = int(self._initialized and setpoint != self.last_setpoint)

        if not self._initialized or setpoint_changed:
            # First-ever tick, or the setpoint moved: the old EWMA was
            # computed against a different target and carries no usable
            # information about the new one, so start clean.
            self.ewma_error = error
        else:
            self.ewma_error = (1.0 - ALPHA) * self.ewma_error + ALPHA * error

        self.last_setpoint = setpoint
        self._initialized = True

        gain = K_DOWN if self.ewma_error >= 0.0 else K_UP
        delta_frac = -gain * self.ewma_error

        new_limit = self.limit * (1.0 + delta_frac)
        new_limit = min(self.max_limit, max(self.min_limit, new_limit))
        self.limit = new_limit

        telemetry = {
            "t": int(obs["t"]),
            "error": error,
            "ewma_error": self.ewma_error,
            "gain": gain,
            "delta_frac": delta_frac,
            "setpoint_changed": setpoint_changed,
            "limit": self.limit,
        }
        return {"limit": self.limit, "telemetry": telemetry}

    def snapshot(self) -> str:
        state = {
            "min_limit": self.min_limit,
            "max_limit": self.max_limit,
            "limit": self.limit,
            "ewma_error": self.ewma_error,
            "last_setpoint": self.last_setpoint,
            "initialized": self._initialized,
        }
        return json.dumps(state)

    @classmethod
    def restore(cls, blob: str) -> "Controller":
        state = json.loads(blob)
        obj = cls({
            "min_limit": state["min_limit"],
            "max_limit": state["max_limit"],
            "initial_limit": state["limit"],
        })
        obj.ewma_error = state["ewma_error"]
        obj.last_setpoint = state["last_setpoint"]
        obj._initialized = state["initialized"]
        return obj
