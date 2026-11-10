"""Adaptive concurrency controller.

It uses a velocity-form (incremental) PI law on normalized latency error:

    e_t   = (setpoint - latency) / setpoint          (positive => headroom)
    de_t  = e_t - e_{t-1}                            (trend; 0 on first sample)
    vol_t = EWMA of de_t^2                           (volatility estimate)
    u_t   = Ki*e_t + Kp*de_t - Kv*sqrt(vol_t)        (clipped to [-1, 1])
    dL    = G * (u_t if u_t > 0 else DOWN_RATIO * u_t)
    L     = clip(L + dL, min_limit, max_limit)

How this meets each requirement:
  * Asymmetric response: DOWN_RATIO = 2.5 (> 2). The drive is computed
    symmetrically, so equal and opposite errors give the same |u|.
  * Trend recency: the Kp*de term rewards a sequence that ends better than it
    started. Improving and worsening orderings of the same samples therefore
    differ by about Kp*(e_last - e_first).
  * Anti-windup: there is no accumulated integrator. The limit is the only
    integrating state, and it is clipped. Each step depends only on the current
    error, the previous error, and a volatility term that decays.
  * Volatility suppression: the volatility penalty and the asymmetric gain both
    push the steady state to a lower limit when the error varies more.
  * Setpoint-change quiet: on a setpoint change, the previous error and the
    volatility state are reset, so pre-change history has no effect.
  * Determinism and round-trip: the code uses only float arithmetic and no
    hashing or randomness. The state is serialized with JSON, and Python's
    float repr round-trips exactly.
  * Telemetry schema: the same fixed set of keys is returned on every tick.
"""

import json
import math


_KI = 0.5          # proportional-on-error (integral in velocity form)
_KP = 1.0          # trend (derivative of error) weight
_KV = 0.2          # volatility penalty weight
_VOL_ALPHA = 0.2   # EWMA factor for volatility
_DOWN_RATIO = 2.5  # downward gain multiplier (> 2)
_GAIN_FRAC = 0.05  # limit units per unit drive, as fraction of range
_E_CLIP = 1.0
_DE_CLIP = 1.0
_U_CLIP = 1.0
_SP_EPS = 1e-9

_STATE_VERSION = 1


def _clip(x, lo, hi):
    if x < lo:
        return lo
    if x > hi:
        return hi
    return x


class Controller:
    def __init__(self, config: dict):
        self.min_limit = float(config.get("min_limit", 1.0))
        self.max_limit = float(config.get("max_limit", 200.0))
        if self.max_limit < self.min_limit:
            self.max_limit = self.min_limit
        init = float(config.get("initial_limit", 50.0))
        self.limit = _clip(init, self.min_limit, self.max_limit)
        self.gain = _GAIN_FRAC * max(self.max_limit - self.min_limit, 1.0)
        self.setpoint = None        # last seen setpoint
        self.prev_e = None          # previous normalized error (post-change)
        self.vol = 0.0              # EWMA of squared error deltas
        self.ticks_since_change = 0
        self.setpoint_changes = 0
        self.ticks = 0

    # ------------------------------------------------------------------ #
    def tick(self, obs: dict) -> dict:
        latency = float(obs.get("latency_ms", 0.0))
        sp = float(obs.get("setpoint_ms", 0.0))
        t = int(obs.get("t", self.ticks))

        if not math.isfinite(latency):
            latency = 1e12 if latency > 0 or math.isnan(latency) else 0.0
        if latency < 0.0:
            latency = 0.0

        # Setpoint change: forget all pre-change history.
        changed = 0
        if self.setpoint is None or sp != self.setpoint:
            if self.setpoint is not None:
                self.setpoint_changes += 1
                changed = 1
            self.setpoint = sp
            self.prev_e = None
            self.vol = 0.0
            self.ticks_since_change = 0

        sp_eff = sp if sp > _SP_EPS else _SP_EPS
        e = _clip((sp_eff - latency) / sp_eff, -_E_CLIP, _E_CLIP)

        if self.prev_e is None:
            de = 0.0
        else:
            de = _clip(e - self.prev_e, -_DE_CLIP, _DE_CLIP)
            self.vol = (1.0 - _VOL_ALPHA) * self.vol + _VOL_ALPHA * de * de

        vol_std = math.sqrt(self.vol) if self.vol > 0.0 else 0.0
        p_term = _KI * e
        d_term = _KP * de
        v_term = _KV * vol_std
        raw = p_term + d_term - v_term
        u = _clip(raw, -_U_CLIP, _U_CLIP)

        if u > 0.0:
            delta = self.gain * u
            direction = "up"
        elif u < 0.0:
            delta = self.gain * _DOWN_RATIO * u
            direction = "down"
        else:
            delta = 0.0
            direction = "hold"

        proposed = self.limit + delta
        new_limit = _clip(proposed, self.min_limit, self.max_limit)
        if new_limit >= self.max_limit:
            sat = "max"
        elif new_limit <= self.min_limit:
            sat = "min"
        else:
            sat = "none"

        self.limit = new_limit
        self.prev_e = e
        self.ticks_since_change += 1
        self.ticks += 1

        telemetry = {
            "t": t,
            "error": e,
            "error_delta": de,
            "volatility": vol_std,
            "p_term": p_term,
            "d_term": d_term,
            "v_term": v_term,
            "drive": u,
            "delta": delta,
            "limit": new_limit,
            "setpoint_ms": sp,
            "latency_ms": latency,
            "setpoint_changed": changed,
            "ticks_since_setpoint_change": self.ticks_since_change,
            "direction": direction,
            "saturation": sat,
        }
        return {"limit": float(new_limit), "telemetry": telemetry}

    # ------------------------------------------------------------------ #
    def snapshot(self) -> str:
        state = {
            "v": _STATE_VERSION,
            "min_limit": self.min_limit,
            "max_limit": self.max_limit,
            "limit": self.limit,
            "gain": self.gain,
            "setpoint": self.setpoint,
            "prev_e": self.prev_e,
            "vol": self.vol,
            "ticks_since_change": self.ticks_since_change,
            "setpoint_changes": self.setpoint_changes,
            "ticks": self.ticks,
        }
        return json.dumps(state, sort_keys=True, allow_nan=True)

    @classmethod
    def restore(cls, blob: str) -> "Controller":
        s = json.loads(blob)
        obj = cls.__new__(cls)
        obj.min_limit = float(s["min_limit"])
        obj.max_limit = float(s["max_limit"])
        obj.limit = float(s["limit"])
        obj.gain = float(s["gain"])
        obj.setpoint = None if s["setpoint"] is None else float(s["setpoint"])
        obj.prev_e = None if s["prev_e"] is None else float(s["prev_e"])
        obj.vol = float(s["vol"])
        obj.ticks_since_change = int(s["ticks_since_change"])
        obj.setpoint_changes = int(s["setpoint_changes"])
        obj.ticks = int(s["ticks"])
        return obj
