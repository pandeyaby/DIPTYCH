"""Adaptive concurrency controller.

Design overview
---------------
The controller works in velocity (incremental) form in log-limit space:

    step_t = -KI * s(e_t)                 # integral-like action
             -KP * (s(e_t) - s_prev)      # proportional action on change (trend)
             -KV * std_t                  # volatility suppression
    limit_{t+1} = clamp(limit_t * exp(clip(step_t)))

where
    e_t     = (latency - setpoint) / setpoint     (normalized error, clipped)
    s(e)    = ASYM * e  if e > 0 else e           (asymmetric shaping, ASYM = 4)
    std_t   = exponentially-weighted std of the shaped error since the last
              setpoint change.

Properties:
  * Asymmetry: shaped error is 4x stronger for overload; per-tick step caps
    (up <= 0.1, down <= 0.6 in log space) keep the downward change of the
    limit above 3x the upward change for equal-magnitude deviations.
  * Recency: the proportional term telescopes to -KP * s(e_last), so the most
    recent observations dominate the ordering effect.
  * Anti-windup: velocity form keeps no unbounded integrator; additionally,
    whenever the output is clamped at a bound the trend reference is reset,
    so the first response after saturation depends only on the new error.
  * Volatility: the std penalty and the asymmetric shaping both push the
    equilibrium toward a lower limit when the error variance is higher.
  * Setpoint change: all error history (trend reference, mean, variance) is
    reset whenever the setpoint changes.
  * Determinism / round-trip: pure float arithmetic, no randomness; snapshot
    stores floats via float.hex for exact restoration.
"""

import json
import math


def _fhex(x):
    return float(x).hex()


def _funhex(s):
    return float.fromhex(s)


class Controller:
    KI = 0.1
    KP = 0.2
    KV = 0.03
    ASYM = 4.0
    ALPHA = 0.1
    UP_CAP = 0.1
    DOWN_CAP = 0.6
    E_MIN = -1.0
    E_MAX = 2.0

    def __init__(self, config: dict):
        cfg = dict(config or {})
        min_limit = float(cfg.get("min_limit", 1.0))
        max_limit = float(cfg.get("max_limit", 200.0))
        if not math.isfinite(min_limit) or min_limit <= 0.0:
            min_limit = 1.0
        if not math.isfinite(max_limit) or max_limit < min_limit:
            max_limit = max(min_limit, 200.0)
        initial = float(cfg.get("initial_limit", 50.0))
        if not math.isfinite(initial):
            initial = (min_limit + max_limit) / 2.0
        initial = min(max(initial, min_limit), max_limit)

        self._min = min_limit
        self._max = max_limit
        self._limit = initial
        self._sp = None          # last seen setpoint (None before first tick)
        self._prev_s = 0.0       # trend reference (shaped error)
        self._mean = 0.0         # EW mean of shaped error (segment)
        self._var = 0.0          # EW variance of shaped error (segment)
        self._n = 0              # observations in current setpoint segment
        self._segments = 0       # number of setpoint segments seen

    # ------------------------------------------------------------------ #
    def _reset_segment(self):
        self._prev_s = 0.0
        self._mean = 0.0
        self._var = 0.0
        self._n = 0
        self._segments += 1

    def tick(self, obs: dict) -> dict:
        # --- read & sanitize inputs ------------------------------------
        try:
            sp = float(obs.get("setpoint_ms", 0.0))
        except (TypeError, ValueError):
            sp = float("nan")
        if not math.isfinite(sp) or sp <= 0.0:
            sp = self._sp if self._sp is not None else 1.0
        try:
            lat = float(obs.get("latency_ms", 0.0))
        except (TypeError, ValueError):
            lat = float("nan")

        # --- setpoint change: forget all pre-change history ------------
        setpoint_changed = 0
        if self._sp is None or sp != self._sp:
            if self._sp is not None:
                setpoint_changed = 1
            self._reset_segment()
            self._sp = sp

        # --- error ------------------------------------------------------
        if math.isfinite(lat):
            e = (lat - sp) / sp
        else:
            e = self.E_MAX
        if e < self.E_MIN:
            e = self.E_MIN
        elif e > self.E_MAX:
            e = self.E_MAX
        s = self.ASYM * e if e > 0.0 else e

        # --- volatility estimate (EW, segment-local) -------------------
        if self._n == 0:
            self._mean = s
            self._var = 0.0
        else:
            delta = s - self._mean
            self._mean = self._mean + self.ALPHA * delta
            self._var = (1.0 - self.ALPHA) * (self._var + self.ALPHA * delta * delta)
        self._n += 1
        std = math.sqrt(self._var) if self._var > 0.0 else 0.0

        # --- control law -----------------------------------------------
        i_term = -self.KI * s
        p_term = -self.KP * (s - self._prev_s)
        v_term = -self.KV * std
        raw_step = i_term + p_term + v_term
        step = raw_step
        if step > self.UP_CAP:
            step = self.UP_CAP
        elif step < -self.DOWN_CAP:
            step = -self.DOWN_CAP

        candidate = self._limit * math.exp(step)
        saturated = 0
        if candidate >= self._max:
            new_limit = self._max
            saturated = 1 if candidate > self._max or step > 0.0 else 0
        elif candidate <= self._min:
            new_limit = self._min
            saturated = -1 if candidate < self._min or step < 0.0 else 0
        else:
            new_limit = candidate
        if not math.isfinite(new_limit):
            new_limit = self._min
        new_limit = min(max(new_limit, self._min), self._max)

        # anti-windup: when clamped, drop the trend reference so the next
        # response depends only on the next observed error
        self._prev_s = 0.0 if saturated != 0 else s
        self._limit = new_limit

        telemetry = {
            "error": e,
            "shaped_error": s,
            "ew_mean": self._mean,
            "ew_std": std,
            "i_term": i_term,
            "p_term": p_term,
            "v_term": v_term,
            "raw_step": raw_step,
            "step": step,
            "saturated": int(saturated),
            "setpoint_ms": sp,
            "setpoint_changed": int(setpoint_changed),
            "segment_ticks": int(self._n),
            "segments": int(self._segments),
            "limit": new_limit,
        }
        return {"limit": float(new_limit), "telemetry": telemetry}

    # ------------------------------------------------------------------ #
    def snapshot(self) -> str:
        state = {
            "v": 1,
            "min": _fhex(self._min),
            "max": _fhex(self._max),
            "limit": _fhex(self._limit),
            "sp": None if self._sp is None else _fhex(self._sp),
            "prev_s": _fhex(self._prev_s),
            "mean": _fhex(self._mean),
            "var": _fhex(self._var),
            "n": int(self._n),
            "segments": int(self._segments),
        }
        return json.dumps(state, sort_keys=True, separators=(",", ":"))

    @classmethod
    def restore(cls, blob: str) -> "Controller":
        st = json.loads(blob)
        min_limit = _funhex(st["min"])
        max_limit = _funhex(st["max"])
        obj = cls({
            "min_limit": min_limit,
            "max_limit": max_limit,
            "initial_limit": _funhex(st["limit"]),
        })
        obj._min = min_limit
        obj._max = max_limit
        obj._limit = _funhex(st["limit"])
        obj._sp = None if st["sp"] is None else _funhex(st["sp"])
        obj._prev_s = _funhex(st["prev_s"])
        obj._mean = _funhex(st["mean"])
        obj._var = _funhex(st["var"])
        obj._n = int(st["n"])
        obj._segments = int(st["segments"])
        return obj
