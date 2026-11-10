"""Adaptive concurrency controller.

Closed-loop controller that adjusts a concurrency limit so that observed
latency tracks a (possibly time-varying) setpoint. Uses an asymmetric,
recency-weighted proportional-integral control law with conditional
integration (anti-windup).
"""

import json


class Controller:
    # Gains: down-gain must strictly exceed 2x up-gain (asymmetric response).
    _KP_DOWN = 3.0
    _KP_UP = 1.0

    # EWMA smoothing factor for the trend term (higher = more recency weight).
    _ALPHA = 0.3

    # Integral gain and clamp (bounded to avoid pathological blow-up).
    _KI = 0.15
    _INTEGRAL_CLAMP = 20.0

    # Clamp on the fractional per-tick correction applied to the limit.
    _CORRECTION_CLAMP = 0.95

    def __init__(self, config: dict):
        config = config or {}
        self.min_limit = float(config.get("min_limit", 1.0))
        self.max_limit = float(config.get("max_limit", 200.0))
        initial_limit = float(config.get("initial_limit", 50.0))

        self.limit = min(max(initial_limit, self.min_limit), self.max_limit)
        self.ewma_signal = 0.0
        self.integral = 0.0
        self.last_setpoint = None

    def tick(self, obs: dict) -> dict:
        t = int(obs["t"])
        latency_ms = float(obs["latency_ms"])
        setpoint_ms = float(obs["setpoint_ms"])
        safe_setpoint = setpoint_ms if abs(setpoint_ms) > 1e-9 else 1e-9

        # Setpoint-change quiet: forget all accumulated state the instant
        # the target changes, so corrections depend only on what follows.
        if self.last_setpoint is not None and setpoint_ms != self.last_setpoint:
            self.ewma_signal = 0.0
            self.integral = 0.0
        self.last_setpoint = setpoint_ms

        error = (latency_ms - setpoint_ms) / safe_setpoint

        # Asymmetric per-sample response: decreases hit much harder than
        # increases for an equal-magnitude, opposite-sign deviation.
        if error >= 0.0:
            raw_signal = self._KP_DOWN * error
        else:
            raw_signal = self._KP_UP * error

        at_max = self.limit >= self.max_limit
        at_min = self.limit <= self.min_limit

        # Anti-windup: freeze integral accumulation while saturated and the
        # current signal would only push further into the same saturation,
        # so a later excursion is answered by its own error, not stale history.
        freeze = (at_max and raw_signal < 0.0) or (at_min and raw_signal > 0.0)

        if not freeze:
            self.integral += raw_signal
            if self.integral > self._INTEGRAL_CLAMP:
                self.integral = self._INTEGRAL_CLAMP
            elif self.integral < -self._INTEGRAL_CLAMP:
                self.integral = -self._INTEGRAL_CLAMP

        # Trend recency: exponential weighting makes the most recent
        # observations dominate the smoothed signal.
        self.ewma_signal = self._ALPHA * raw_signal + (1.0 - self._ALPHA) * self.ewma_signal

        correction = self.ewma_signal + self._KI * self.integral
        if correction > self._CORRECTION_CLAMP:
            correction = self._CORRECTION_CLAMP
        elif correction < -self._CORRECTION_CLAMP:
            correction = -self._CORRECTION_CLAMP

        new_limit = self.limit * (1.0 - correction)
        if new_limit > self.max_limit:
            new_limit = self.max_limit
        elif new_limit < self.min_limit:
            new_limit = self.min_limit

        self.limit = new_limit

        if new_limit >= self.max_limit:
            saturated = "max"
        elif new_limit <= self.min_limit:
            saturated = "min"
        else:
            saturated = "none"

        telemetry = {
            "t": t,
            "limit": float(new_limit),
            "error": float(error),
            "raw_signal": float(raw_signal),
            "ewma_signal": float(self.ewma_signal),
            "integral": float(self.integral),
            "correction": float(correction),
            "saturated": saturated,
            "setpoint_ms": float(setpoint_ms),
            "freeze_integral": 1 if freeze else 0,
        }

        return {"limit": float(new_limit), "telemetry": telemetry}

    def snapshot(self) -> str:
        state = {
            "min_limit": self.min_limit,
            "max_limit": self.max_limit,
            "limit": self.limit,
            "ewma_signal": self.ewma_signal,
            "integral": self.integral,
            "last_setpoint": self.last_setpoint,
        }
        return json.dumps(state, sort_keys=True)

    @classmethod
    def restore(cls, blob: str) -> "Controller":
        state = json.loads(blob)
        obj = cls.__new__(cls)
        obj.min_limit = float(state["min_limit"])
        obj.max_limit = float(state["max_limit"])
        obj.limit = float(state["limit"])
        obj.ewma_signal = float(state["ewma_signal"])
        obj.integral = float(state["integral"])
        obj.last_setpoint = (
            None if state["last_setpoint"] is None else float(state["last_setpoint"])
        )
        return obj
