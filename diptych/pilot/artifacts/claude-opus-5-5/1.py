"""Adaptive concurrency controller.

Keeps observed latency near a setpoint by adjusting a concurrency limit.

Design summary
--------------
* The normalized error is e = (latency - setpoint) / setpoint, clipped to
  [-1, 1]. It is then shaped asymmetrically: g(e) = K_DOWN * e for e > 0 and
  K_UP * e for e < 0, with K_DOWN = 4 * K_UP. Every corrective term is driven
  by g, so a downward correction is always more than twice the matching
  upward one.
* An additive integrator ("base") steps by -STEP * g(e) on each tick. The
  step is additive, so its size does not depend on the current limit or on
  past history. The integrator is clamped to [min_limit, max_limit].
* A proportional term uses an exponentially weighted moving average (EWMA)
  of g, plus an EWMA trend term on the change in error. Both give recent
  observations more weight than older ones.
* A volatility penalty subtracts a term proportional to the EW standard
  deviation of the error and to the coefficient of variation of arrivals.
* Anti-windup: while the integrator sits at a bound and the error pushes it
  further into that bound, all state is frozen. Saturation duration
  therefore has no effect on later decisions.
* When the setpoint changes, the current output is folded into the
  integrator and all history-dependent state is reset. Later corrections
  then depend only on observations made after the change.
* All arithmetic is deterministic float math. Snapshots use JSON, and the
  float repr round-trips exactly.
"""

import json
import math

K_UP = 0.4          # shaping gain for latency below setpoint (raise limit)
K_DOWN = 1.6        # shaping gain for latency above setpoint (cut limit)
STEP_FRAC = 0.04    # integrator step as a fraction of the limit range
ALPHA = 0.3         # EWMA weight for proportional and trend terms
BETA = 0.1          # EW weight for mean/variance estimates
KP = 1.0            # proportional gain
KT = 1.0            # trend gain
KV = 2.0            # error-volatility penalty gain
KVA = 2.0           # arrival-volatility penalty gain

_STATE_VERSION = 1


def _clip(x, lo, hi):
    if x < lo:
        return lo
    if x > hi:
        return hi
    return x


def _shape(x):
    return K_DOWN * x if x > 0.0 else K_UP * x


def _finite(x, default=0.0):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return default
    if math.isnan(x) or math.isinf(x):
        return default
    return x


class Controller:
    def __init__(self, config: dict):
        lo = float(config.get("min_limit", 1.0))
        hi = float(config.get("max_limit", 200.0))
        if hi < lo:
            lo, hi = hi, lo
        init = float(config.get("initial_limit", 50.0))
        self.min_limit = lo
        self.max_limit = hi
        self.initial_limit = _clip(init, lo, hi)
        self.step = STEP_FRAC * (hi - lo)

        self.base = self.initial_limit
        self.last_limit = self.initial_limit
        self.setpoint = None
        self.ticks = 0
        self.resets = 0
        self._reset_history()

    # ------------------------------------------------------------------ state
    def _reset_history(self):
        self.n = 0
        self.ewma_g = 0.0
        self.trend = 0.0
        self.prev_e = None
        self.mean_e = 0.0
        self.var_e = 0.0
        self.arr_n = 0
        self.arr_mean = 0.0
        self.arr_var = 0.0

    def _state(self):
        return {
            "v": _STATE_VERSION,
            "min_limit": self.min_limit,
            "max_limit": self.max_limit,
            "initial_limit": self.initial_limit,
            "step": self.step,
            "base": self.base,
            "last_limit": self.last_limit,
            "setpoint": self.setpoint,
            "ticks": self.ticks,
            "resets": self.resets,
            "n": self.n,
            "ewma_g": self.ewma_g,
            "trend": self.trend,
            "prev_e": self.prev_e,
            "mean_e": self.mean_e,
            "var_e": self.var_e,
            "arr_n": self.arr_n,
            "arr_mean": self.arr_mean,
            "arr_var": self.arr_var,
        }

    def snapshot(self) -> str:
        return json.dumps(self._state(), sort_keys=True, allow_nan=True)

    @classmethod
    def restore(cls, blob: str) -> "Controller":
        s = json.loads(blob)
        obj = cls.__new__(cls)
        obj.min_limit = float(s["min_limit"])
        obj.max_limit = float(s["max_limit"])
        obj.initial_limit = float(s["initial_limit"])
        obj.step = float(s["step"])
        obj.base = float(s["base"])
        obj.last_limit = float(s["last_limit"])
        obj.setpoint = None if s["setpoint"] is None else float(s["setpoint"])
        obj.ticks = int(s["ticks"])
        obj.resets = int(s["resets"])
        obj.n = int(s["n"])
        obj.ewma_g = float(s["ewma_g"])
        obj.trend = float(s["trend"])
        obj.prev_e = None if s["prev_e"] is None else float(s["prev_e"])
        obj.mean_e = float(s["mean_e"])
        obj.var_e = float(s["var_e"])
        obj.arr_n = int(s["arr_n"])
        obj.arr_mean = float(s["arr_mean"])
        obj.arr_var = float(s["arr_var"])
        return obj

    # ------------------------------------------------------------------- tick
    def tick(self, obs: dict) -> dict:
        self.ticks += 1
        sp = _finite(obs.get("setpoint_ms", 0.0), 0.0)
        if sp <= 0.0:
            sp = self.setpoint if self.setpoint is not None else 1.0
        lat = _finite(obs.get("latency_ms", sp), sp)
        if lat < 0.0:
            lat = 0.0
        arr = _finite(obs.get("arrival_rps", 0.0), 0.0)
        if arr < 0.0:
            arr = 0.0

        # Setpoint change: fold the current output into the integrator and
        # forget all history from before the change.
        if self.setpoint is not None and sp != self.setpoint:
            self.base = _clip(self.last_limit, self.min_limit, self.max_limit)
            self._reset_history()
            self.resets += 1
        self.setpoint = sp

        e = _clip((lat - sp) / sp, -1.0, 1.0)
        g = _shape(e)

        # Anti-windup: freeze all state while pushing further into a bound.
        frozen = (self.base >= self.max_limit and e < 0.0) or (
            self.base <= self.min_limit and e > 0.0
        )

        if not frozen:
            self.n += 1
            # Recency-weighted shaped error.
            self.ewma_g += ALPHA * (g - self.ewma_g)
            # Trend on the change in error, shaped asymmetrically.
            de = 0.0 if self.prev_e is None else e - self.prev_e
            self.trend += ALPHA * (_shape(de) - self.trend)
            self.prev_e = e
            # EW mean/variance of the error.
            if self.n == 1:
                self.mean_e = e
                self.var_e = 0.0
            else:
                d = e - self.mean_e
                self.mean_e += BETA * d
                self.var_e = (1.0 - BETA) * (self.var_e + BETA * d * d)
            # EW mean/variance of arrivals.
            self.arr_n += 1
            if self.arr_n == 1:
                self.arr_mean = arr
                self.arr_var = 0.0
            else:
                d = arr - self.arr_mean
                self.arr_mean += BETA * d
                self.arr_var = (1.0 - BETA) * (self.arr_var + BETA * d * d)
            # Clamped integrator (additive step).
            self.base = _clip(
                self.base - self.step * g, self.min_limit, self.max_limit
            )

        std_e = math.sqrt(self.var_e) if self.var_e > 0.0 else 0.0
        if self.arr_mean > 1e-12 and self.arr_var > 0.0:
            arr_cv = math.sqrt(self.arr_var) / self.arr_mean
        else:
            arr_cv = 0.0
        if arr_cv > 1.0:
            arr_cv = 1.0

        p_term = -self.step * (KP * self.ewma_g + KT * self.trend)
        v_term = self.step * (KV * std_e + KVA * arr_cv)
        limit = _clip(self.base + p_term - v_term, self.min_limit, self.max_limit)
        limit = float(limit)
        self.last_limit = limit

        telemetry = {
            "tick": int(self.ticks),
            "error": float(e),
            "shaped_error": float(g),
            "base": float(self.base),
            "p_term": float(p_term),
            "v_term": float(v_term),
            "ewma_g": float(self.ewma_g),
            "trend": float(self.trend),
            "mean_e": float(self.mean_e),
            "std_e": float(std_e),
            "arr_mean": float(self.arr_mean),
            "arr_cv": float(arr_cv),
            "frozen": int(1 if frozen else 0),
            "samples_since_reset": int(self.n),
            "setpoint_ms": float(sp),
            "setpoint_resets": int(self.resets),
            "mode": "frozen" if frozen else "active",
        }
        return {"limit": limit, "telemetry": telemetry}
