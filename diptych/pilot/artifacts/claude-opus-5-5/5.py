"""Adaptive concurrency controller.

Velocity-form PD controller on normalised latency error with:
  * asymmetric gains (down gain = 3x up gain),
  * a derivative term so recent observations dominate (trend recency),
  * no integral accumulator; the limit itself is clamped (anti-windup), and
    while saturated at max_limit filter state is frozen / cleared,
  * an EWMA-variance volatility penalty,
  * full filter reset on setpoint change,
  * JSON snapshot with exact float round-trip.
"""

import json
import math


class Controller:
    K_UP = 0.05          # fraction of range per unit error, upward
    K_DOWN = 0.15        # fraction of range per unit error, downward (3x up)
    BETA = 0.5           # derivative (trend) weight
    ALPHA = 0.1          # EWMA factor for volatility stats
    K_VOL = 0.03         # volatility penalty gain (fraction of range per unit std)
    SIG_CLIP = 2.0       # clip on control signal magnitude

    def __init__(self, config: dict):
        self.min_limit = float(config.get("min_limit", 1.0))
        self.max_limit = float(config.get("max_limit", 200.0))
        if self.max_limit < self.min_limit:
            self.max_limit = self.min_limit
        init = float(config.get("initial_limit", 50.0))
        self.limit = self._clamp(init)
        self.scale = max(self.max_limit - self.min_limit, 1.0)

        self.e_prev = None
        self.ew_mean = 0.0
        self.ew_var = 0.0
        self.n = 0
        self.last_setpoint = None
        self.saturated = False
        self.ticks = 0
        self.since_reset = 0

    # ------------------------------------------------------------------ utils
    def _clamp(self, x):
        if x != x:  # NaN guard
            return self.min_limit
        if x < self.min_limit:
            return self.min_limit
        if x > self.max_limit:
            return self.max_limit
        return x

    def _reset_filters(self):
        self.e_prev = None
        self.ew_mean = 0.0
        self.ew_var = 0.0
        self.n = 0
        self.saturated = False
        self.since_reset = 0

    def _update_stats(self, e):
        if self.n == 0:
            self.ew_mean = e
            self.ew_var = 0.0
        else:
            d = e - self.ew_mean
            incr = self.ALPHA * d
            self.ew_mean = self.ew_mean + incr
            self.ew_var = (1.0 - self.ALPHA) * (self.ew_var + d * incr)
        self.n += 1

    # ------------------------------------------------------------------- tick
    def tick(self, obs: dict) -> dict:
        self.ticks += 1
        latency = float(obs.get("latency_ms", 0.0))
        setpoint = float(obs.get("setpoint_ms", 1.0))
        if not (setpoint > 0.0):
            setpoint = 1e-9
        if latency != latency or latency < 0.0:
            latency = 0.0

        reset = 0
        if self.last_setpoint is not None and setpoint != self.last_setpoint:
            self._reset_filters()
            reset = 1
        self.last_setpoint = setpoint

        e = (latency - setpoint) / setpoint
        if e > self.SIG_CLIP:
            e = self.SIG_CLIP

        signal = 0.0
        vol_pen = 0.0
        delta = 0.0

        if self.saturated and e <= 0.0:
            # Still saturated: freeze all filter state so that nothing
            # accumulates with saturation duration.
            self.e_prev = None
            self.limit = self.max_limit
        else:
            leaving_saturation = self.saturated
            self.saturated = False

            if self.e_prev is None:
                signal = e
            else:
                signal = e + self.BETA * (e - self.e_prev)
            if signal > self.SIG_CLIP:
                signal = self.SIG_CLIP
            elif signal < -self.SIG_CLIP:
                signal = -self.SIG_CLIP

            if signal > 0.0:
                delta = -self.K_DOWN * signal * self.scale
            else:
                delta = -self.K_UP * signal * self.scale

            if not leaving_saturation and self.n >= 2:
                vol_pen = self.K_VOL * math.sqrt(max(self.ew_var, 0.0)) * self.scale
                delta -= vol_pen

            self._update_stats(e)
            self.e_prev = e

            new_limit = self._clamp(self.limit + delta)
            if new_limit >= self.max_limit and delta > 0.0:
                self.saturated = True
            self.limit = new_limit

        self.since_reset += 1

        telemetry = {
            "error": float(e),
            "signal": float(signal),
            "delta": float(delta),
            "vol_penalty": float(vol_pen),
            "ewma_mean_err": float(self.ew_mean),
            "ewma_var_err": float(self.ew_var),
            "samples": int(self.n),
            "saturated": int(1 if self.saturated else 0),
            "setpoint_reset": int(reset),
            "setpoint_ms": float(setpoint),
            "ticks": int(self.ticks),
            "ticks_since_reset": int(self.since_reset),
            "limit": float(self.limit),
        }
        return {"limit": float(self.limit), "telemetry": telemetry}

    # -------------------------------------------------------------- snapshot
    def snapshot(self) -> str:
        state = {
            "min_limit": self.min_limit,
            "max_limit": self.max_limit,
            "limit": self.limit,
            "scale": self.scale,
            "e_prev": self.e_prev,
            "ew_mean": self.ew_mean,
            "ew_var": self.ew_var,
            "n": self.n,
            "last_setpoint": self.last_setpoint,
            "saturated": self.saturated,
            "ticks": self.ticks,
            "since_reset": self.since_reset,
        }
        return json.dumps(state, sort_keys=True)

    @classmethod
    def restore(cls, blob: str) -> "Controller":
        s = json.loads(blob)
        c = cls({
            "min_limit": s["min_limit"],
            "max_limit": s["max_limit"],
            "initial_limit": s["limit"],
        })
        c.limit = float(s["limit"])
        c.scale = float(s["scale"])
        c.e_prev = None if s["e_prev"] is None else float(s["e_prev"])
        c.ew_mean = float(s["ew_mean"])
        c.ew_var = float(s["ew_var"])
        c.n = int(s["n"])
        c.last_setpoint = None if s["last_setpoint"] is None else float(s["last_setpoint"])
        c.saturated = bool(s["saturated"])
        c.ticks = int(s["ticks"])
        c.since_reset = int(s["since_reset"])
        return c
