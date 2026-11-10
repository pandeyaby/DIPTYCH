"""Adaptive concurrency controller.

A multiplicative controller driven by a blend of the instantaneous
normalized latency error and a recency-weighted (exponential) moving
average of that error, plus an explicit volatility margin. The response
is asymmetric: a latency excursion above setpoint triggers a strictly
larger multiplicative cut than an equal-and-opposite excursion below
setpoint triggers a raise. The EMA accumulators use integrator clamping
(frozen while the limit sits at a bound and the current error would only
deepen that saturation) so that a long run of saturation does not build
up a bias that would otherwise make the first response to a new
excursion depend on how long the controller had been saturated.

A running estimate of error variance is fed back as an always-downward
bias on the control signal, so that noisier error streams settle at a
strictly lower limit than calmer ones with the same mean error -- this
holds regardless of whether the extra noise happens to show up mostly as
latency spikes or as dips (which, under an admission-capped plant, is not
guaranteed by asymmetric averaging alone).
"""

from __future__ import annotations

import json

# Gains: DOWN_GAIN must exceed 2 * UP_GAIN (requirement 1), with margin
# for floating point. EMA_ALPHA controls how fast older samples decay out
# of the trend accumulator (requirement 2). BLEND weights how much the
# correction leans on the instantaneous reading vs. the trend.
DOWN_GAIN = 0.15
UP_GAIN = 0.065
EMA_ALPHA = 0.20
EMA_CLIP = 1.0
BLEND_INSTANT = 0.5
BLEND_TREND = 0.5
VAR_ALPHA = 0.30
VOL_GAIN = 1.0
MIN_FACTOR = 0.5
MAX_FACTOR = 1.5
SAT_EPS = 1e-9


class Controller:
    def __init__(self, config: dict):
        self.min_limit = float(config["min_limit"])
        self.max_limit = float(config["max_limit"])
        self.limit = float(config["initial_limit"])
        self.limit = min(max(self.limit, self.min_limit), self.max_limit)

        self.ema_err = 0.0
        self.ema_var = 0.0
        self.trend_initialized = False
        self.setpoint_prev = None
        self.ticks = 0

    # -- core control law -------------------------------------------------

    def tick(self, obs: dict) -> dict:
        setpoint = float(obs["setpoint_ms"])
        latency = float(obs["latency_ms"])

        # Requirement 5: setpoint changes wipe the trend accumulator so
        # corrections depend only on post-change observations.
        setpoint_changed = self.setpoint_prev is not None and setpoint != self.setpoint_prev
        if setpoint_changed:
            self.ema_err = 0.0
            self.ema_var = 0.0
            self.trend_initialized = False
        self.setpoint_prev = setpoint

        e = (latency - setpoint) / setpoint if setpoint > 0 else 0.0

        at_max = self.limit >= self.max_limit - SAT_EPS
        at_min = self.limit <= self.min_limit + SAT_EPS

        # Requirement 3 (anti-windup): freeze the trend accumulator while
        # saturated in the direction the current error would deepen, so it
        # never grows with how long we've been saturated. A fresh error in
        # the opposite direction updates the accumulator immediately.
        frozen = (at_max and e < 0.0) or (at_min and e > 0.0)
        if not frozen:
            e_for_trend = min(max(e, -EMA_CLIP), EMA_CLIP)
            if not self.trend_initialized:
                # First real sample: seed the trend with it rather than
                # treating it as a deviation from an arbitrary zero prior,
                # which would otherwise manufacture a bogus variance
                # estimate out of a single observation.
                self.ema_err = e_for_trend
                self.trend_initialized = True
            else:
                deviation = e_for_trend - self.ema_err
                self.ema_err = EMA_ALPHA * e_for_trend + (1.0 - EMA_ALPHA) * self.ema_err
                self.ema_var = VAR_ALPHA * (deviation * deviation) + (1.0 - VAR_ALPHA) * self.ema_var

        volatility = self.ema_var ** 0.5
        signal = BLEND_INSTANT * e + BLEND_TREND * self.ema_err + VOL_GAIN * volatility

        # Requirement 1 (asymmetric response): strictly larger cut than
        # raise for equal-magnitude opposite signals.
        if signal >= 0.0:
            factor = 1.0 - DOWN_GAIN * signal
        else:
            factor = 1.0 - UP_GAIN * signal

        factor = min(max(factor, MIN_FACTOR), MAX_FACTOR)

        new_limit = self.limit * factor
        new_limit = min(max(new_limit, self.min_limit), self.max_limit)
        self.limit = new_limit
        self.ticks += 1

        telemetry = {
            "error": e,
            "ema_error": self.ema_err,
            "volatility": volatility,
            "signal": signal,
            "factor": factor,
            "frozen": 1.0 if frozen else 0.0,
            "setpoint_changed": 1.0 if setpoint_changed else 0.0,
            "ticks": float(self.ticks),
        }
        return {"limit": self.limit, "telemetry": telemetry}

    # -- persistence --------------------------------------------------------

    def snapshot(self) -> str:
        state = {
            "min_limit": self.min_limit,
            "max_limit": self.max_limit,
            "limit": self.limit,
            "ema_err": self.ema_err,
            "ema_var": self.ema_var,
            "trend_initialized": self.trend_initialized,
            "setpoint_prev": self.setpoint_prev,
            "ticks": self.ticks,
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
        obj.ema_err = state["ema_err"]
        obj.ema_var = state["ema_var"]
        obj.trend_initialized = state["trend_initialized"]
        obj.setpoint_prev = state["setpoint_prev"]
        obj.ticks = state["ticks"]
        return obj
