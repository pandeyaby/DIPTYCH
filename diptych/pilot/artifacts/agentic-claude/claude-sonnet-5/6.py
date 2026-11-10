"""Adaptive concurrency controller.

Regulates a concurrency limit so that observed latency tracks a latency
setpoint. Two recency-weighted signals of the relative latency error drive
the decision each tick:

  * ``level``    -- an EWMA of the error itself (how far off we are).
  * ``momentum`` -- an EWMA of the *change* in error from tick to tick (are
                     we getting better or worse lately).

Combining them is what gives recent observations outsized influence on the
outcome: a low-pass filter on the error level alone is a lagging indicator
(a monotonically improving run still drags a stale, worse-looking average
behind it, and a monotonically worsening run drags a stale, better-looking
one), so by itself it would make a recently-improving run end up *more*
throttled than a recently-worsening run with the same observations in
reverse order. The momentum term corrects that: it is positive while things
are getting worse and negative while things are getting better, regardless
of the absolute error level, so it pushes the limit in the direction recent
history actually favors.

The combined signal feeds one asymmetric multiplicative step: latency above
setpoint (too slow) is corrected much harder than latency below setpoint
(too fast). That asymmetry is also what makes the controller converge to a
more conservative limit under higher-variance load -- costly latency spikes
get punished more than symmetric slack is rewarded, so the equilibrium limit
has to sit lower to keep spikes rare.

Only the Python standard library is used; no I/O, threads, or clock access.
"""

from __future__ import annotations

import json


class Controller:
    # Recency weighting for the two trend signals (higher = more weight on
    # the latest observation, less memory of older ones).
    ALPHA_LEVEL = 0.35
    ALPHA_MOMENTUM = 0.1

    # How strongly the momentum (direction-of-change) signal perturbs the
    # level signal before the asymmetric step is applied.
    MOMENTUM_WEIGHT = 2.5

    # Multiplicative gains. KD (downward, latency too high) must be more
    # than twice KI (upward, latency too low) to satisfy the
    # asymmetric-response requirement for deviations of equal magnitude and
    # opposite sign.
    KD = 0.55
    KI = 0.18

    # The plant's queueing latency is steeply convex near capacity, so a
    # single tick's relative error can spike by a large multiple of the
    # setpoint. Clipping the error before it feeds the trend signals keeps a
    # single extreme reading from causing a one-tick overreaction; sustained
    # bad ticks still drive the (bounded) signals to their limits quickly.
    ERROR_CLIP = 1.0

    # Guard rail on the per-tick multiplicative step, so float edge cases
    # can never send the limit negative or blow up to infinity.
    FACTOR_MIN = 0.05
    FACTOR_MAX = 3.0

    def __init__(self, config: dict):
        self.min_limit = float(config["min_limit"])
        self.max_limit = float(config["max_limit"])
        self.limit = float(config["initial_limit"])
        self.limit = max(self.min_limit, min(self.max_limit, self.limit))
        self.level = 0.0
        self.momentum = 0.0
        self.prev_error = 0.0
        self.prev_setpoint = None
        self.initialized = False

    def tick(self, obs: dict) -> dict:
        setpoint = float(obs["setpoint_ms"])
        latency = float(obs["latency_ms"])
        t = int(obs["t"])

        error = (latency - setpoint) / setpoint
        clipped = max(-self.ERROR_CLIP, min(self.ERROR_CLIP, error))

        # A setpoint change (or the very first tick) discards prior trend
        # memory: corrections from here on depend only on observations made
        # after the change. Resetting to the neutral baseline (rather than
        # to the raw error) also keeps every subsequent observation's
        # weight in the level EWMA strictly recency-ordered -- seeding with
        # the raw error would otherwise hand that single first post-reset
        # tick more weight than later ones for a few ticks.
        reset = (not self.initialized) or (self.prev_setpoint is None) or (setpoint != self.prev_setpoint)
        if reset:
            self.level = 0.0
            self.momentum = 0.0
            self.prev_error = clipped

        delta = clipped - self.prev_error
        self.prev_error = clipped

        self.level = self.ALPHA_LEVEL * clipped + (1.0 - self.ALPHA_LEVEL) * self.level
        self.momentum = self.ALPHA_MOMENTUM * delta + (1.0 - self.ALPHA_MOMENTUM) * self.momentum

        self.prev_setpoint = setpoint
        self.initialized = True

        combined = self.level + self.MOMENTUM_WEIGHT * self.momentum

        if combined >= 0.0:
            factor = 1.0 - self.KD * combined
        else:
            factor = 1.0 + self.KI * (-combined)
        factor = max(self.FACTOR_MIN, min(self.FACTOR_MAX, factor))

        new_limit = self.limit * factor
        new_limit = max(self.min_limit, min(self.max_limit, new_limit))
        self.limit = new_limit

        saturated = 1 if (new_limit <= self.min_limit or new_limit >= self.max_limit) else 0

        telemetry = {
            "error": error,
            "level": self.level,
            "momentum": self.momentum,
            "factor": factor,
            "limit": new_limit,
            "saturated": saturated,
            "t": t,
        }
        return {"limit": new_limit, "telemetry": telemetry}

    def snapshot(self) -> str:
        state = {
            "min_limit": self.min_limit,
            "max_limit": self.max_limit,
            "limit": self.limit,
            "level": self.level,
            "momentum": self.momentum,
            "prev_error": self.prev_error,
            "prev_setpoint": self.prev_setpoint,
            "initialized": self.initialized,
        }
        return json.dumps(state)

    @classmethod
    def restore(cls, blob: str) -> "Controller":
        state = json.loads(blob)
        obj = cls(
            {
                "min_limit": state["min_limit"],
                "max_limit": state["max_limit"],
                "initial_limit": state["limit"],
            }
        )
        obj.limit = state["limit"]
        obj.level = state["level"]
        obj.momentum = state["momentum"]
        obj.prev_error = state["prev_error"]
        obj.prev_setpoint = state["prev_setpoint"]
        obj.initialized = state["initialized"]
        return obj
