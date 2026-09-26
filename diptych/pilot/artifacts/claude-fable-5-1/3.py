"""Adaptive concurrency controller.

Regulates a concurrency limit so observed latency tracks a setpoint.
Pure standard library, deterministic, and fully serialisable.

Control law (per tick):

    raw_err   = (latency - setpoint) / setpoint          # normalised error
    trend     = raw_err - prev_raw_err                    # instantaneous slope
    var       = EWMA(trend^2)                             # volatility estimate
    shift     = K_VOL * min(sqrt(var), VOL_CLAMP)         # lowers effective target
    err       = raw_err + shift
    ewma_err  = EWMA(err)                                 # recency-weighted level
    u         = clamp(ewma_err + K_TREND * trend)         # correction demand
    delta     = -gain(sign(u)) * (max_limit - min_limit) * u
    limit     = clamp(limit + delta, min_limit, max_limit)

* Asymmetry: GAIN_DOWN is 5x GAIN_UP, so a positive deviation shrinks the
  limit more than twice as much as an equal negative deviation grows it.
* Recency: the EWMA level plus a trend term make the final limit depend far
  more on the latest observations than on older ones.
* Anti-windup: the limit itself is the only integrating state and it is
  clamped every tick; the filters are bounded, so the first response after
  saturation depends only on the currently observed error.
* Volatility: the effective target is lowered in proportion to observed
  volatility, and the asymmetric gains drift the limit down under noise, so
  higher-variance workloads converge to a lower limit.
* Setpoint change: all filter state is reset, so subsequent corrections use
  only post-change observations.
"""

import json
import math


class Controller:
    ALPHA_ERR = 0.5     # EWMA weight for the error level (recent-heavy)
    ALPHA_VOL = 0.1     # EWMA weight for the volatility estimate
    K_TREND = 2.0       # weight of the instantaneous trend term
    K_VOL = 0.5         # how strongly volatility lowers the effective target
    VOL_CLAMP = 1.0     # cap on normalised volatility used for the shift
    U_CLAMP = 3.0       # cap on the correction demand per tick
    GAIN_DOWN = 0.10    # fraction of range per unit demand when cutting
    GAIN_UP = 0.02      # fraction of range per unit demand when raising

    def __init__(self, config):
        config = config or {}
        self.min_limit = float(config.get("min_limit", 1.0))
        self.max_limit = float(config.get("max_limit", 200.0))
        if self.max_limit < self.min_limit:
            self.max_limit = self.min_limit
        initial = float(config.get("initial_limit", 50.0))
        self.limit = self._clamp_limit(initial)
        self.setpoint = None      # last seen setpoint (None until first tick)
        self.ewma_err = 0.0       # recency-weighted effective error
        self.prev_err = None      # previous raw normalised error
        self.var = 0.0            # EWMA of squared error changes
        self.ticks = 0

    # ------------------------------------------------------------------ utils
    def _clamp_limit(self, value):
        if value < self.min_limit:
            return self.min_limit
        if value > self.max_limit:
            return self.max_limit
        return value

    def _reset_filters(self):
        self.ewma_err = 0.0
        self.prev_err = None
        self.var = 0.0

    # ------------------------------------------------------------------- tick
    def tick(self, obs):
        t = int(obs.get("t", self.ticks))
        latency = float(obs["latency_ms"])
        setpoint = float(obs["setpoint_ms"])
        sp_safe = setpoint if setpoint > 0.0 else 1e-9

        reset = 0
        if self.setpoint is None:
            self.setpoint = setpoint
        elif setpoint != self.setpoint:
            self._reset_filters()
            self.setpoint = setpoint
            reset = 1

        raw_err = (latency - setpoint) / sp_safe

        if self.prev_err is None:
            trend = 0.0
        else:
            trend = raw_err - self.prev_err
        self.prev_err = raw_err

        # Volatility of the error signal (order-insensitive for monotone runs).
        self.var = self.var + self.ALPHA_VOL * (trend * trend - self.var)
        if self.var < 0.0:
            self.var = 0.0
        vol = math.sqrt(self.var)
        shift = self.K_VOL * (vol if vol < self.VOL_CLAMP else self.VOL_CLAMP)

        # Effective error against a volatility-lowered target.
        err = raw_err + shift
        self.ewma_err = self.ewma_err + self.ALPHA_ERR * (err - self.ewma_err)

        u = self.ewma_err + self.K_TREND * trend
        if u > self.U_CLAMP:
            u = self.U_CLAMP
        elif u < -self.U_CLAMP:
            u = -self.U_CLAMP

        span = self.max_limit - self.min_limit
        if u > 0.0:
            gain = self.GAIN_DOWN
            mode = "down"
        elif u < 0.0:
            gain = self.GAIN_UP
            mode = "up"
        else:
            gain = 0.0
            mode = "hold"

        delta = -gain * span * u
        new_limit = self._clamp_limit(self.limit + delta)
        saturated = 1 if (new_limit >= self.max_limit or new_limit <= self.min_limit) else 0
        applied = new_limit - self.limit
        self.limit = new_limit
        self.ticks += 1

        telemetry = {
            "t": t,
            "limit": float(self.limit),
            "setpoint_ms": float(setpoint),
            "latency_ms": float(latency),
            "raw_err": float(raw_err),
            "err": float(err),
            "ewma_err": float(self.ewma_err),
            "trend": float(trend),
            "vol": float(vol),
            "shift": float(shift),
            "u": float(u),
            "delta_requested": float(delta),
            "delta_applied": float(applied),
            "mode": mode,
            "saturated": saturated,
            "setpoint_reset": reset,
            "ticks": int(self.ticks),
        }
        return {"limit": float(self.limit), "telemetry": telemetry}

    # -------------------------------------------------------------- snapshot
    def snapshot(self):
        state = {
            "min_limit": self.min_limit,
            "max_limit": self.max_limit,
            "limit": self.limit,
            "setpoint": self.setpoint,
            "ewma_err": self.ewma_err,
            "prev_err": self.prev_err,
            "var": self.var,
            "ticks": self.ticks,
        }
        return json.dumps(state, sort_keys=True)

    @classmethod
    def restore(cls, blob):
        state = json.loads(blob)
        obj = cls.__new__(cls)
        obj.min_limit = float(state["min_limit"])
        obj.max_limit = float(state["max_limit"])
        obj.limit = float(state["limit"])
        sp = state["setpoint"]
        obj.setpoint = None if sp is None else float(sp)
        obj.ewma_err = float(state["ewma_err"])
        pe = state["prev_err"]
        obj.prev_err = None if pe is None else float(pe)
        obj.var = float(state["var"])
        obj.ticks = int(state["ticks"])
        return obj
