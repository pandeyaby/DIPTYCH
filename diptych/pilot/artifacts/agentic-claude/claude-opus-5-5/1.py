"""Adaptive concurrency controller.

Structure: a PI controller on normalized latency error.

  output = anchor + I + KP * ewma(f(e))

* ``e = (sp_eff - latency) / sp_eff`` clipped to [-1, 1]; ``f`` scales negative
  errors (latency above setpoint) by ``DOWN_RATIO`` (> 2) -> asymmetric response.
* The proportional term acts on an EWMA of the error, so recent observations
  outweigh older ones (trend recency); the integral term is order-invariant.
* Anti-windup by back-calculation: whenever the output is clamped, ``I`` is
  reset so that ``I + P`` equals the clamped output, so saturation time never
  accumulates.
* Volatility suppression: ``sp_eff = sp * (1 - VOL_GAIN * cv)`` where ``cv`` is
  the EWMA coefficient of variation of offered load (from past ticks only).
* On a setpoint change all history-derived state (EWMA error, load statistics)
  is discarded; only the current limit is carried over.
* State is plain floats serialized with JSON (repr round-trips exactly).
"""

from __future__ import annotations

import json

KI_UP = 2.0          # limit units per tick per unit of (positive) error
KP_UP = 6.0          # proportional gain on EWMA error
DOWN_RATIO = 3.0     # downward corrections are 3x upward ones (must be > 2)
ERR_ALPHA = 0.3      # EWMA weight of newest error
VOL_ALPHA = 0.05     # EWMA weight for load mean / variance
VOL_GAIN = 0.8       # setpoint reduction per unit coefficient of variation
VOL_MAX_CUT = 0.3    # never reduce the effective setpoint by more than 30%
ERR_CLIP = 1.0
GRID = 1048576.0     # limits live on a 2**-20 grid so corrections are exact

_STATE_KEYS = (
    "min_limit", "max_limit", "limit", "anchor", "integ", "ewma_err", "prev_sp",
    "load_mean", "load_var", "load_n", "n",
)


def _shape(e: float) -> float:
    return e if e >= 0.0 else DOWN_RATIO * e


class Controller:
    def __init__(self, config: dict):
        self.min_limit = float(config.get("min_limit", 1.0))
        self.max_limit = float(config.get("max_limit", 200.0))
        init = float(config.get("initial_limit", 50.0))
        self.limit = round(min(max(init, self.min_limit), self.max_limit) * GRID) / GRID
        self.anchor = self.limit  # limit at the last (re)start; integ is relative to it
        self.integ = 0.0
        self.ewma_err = 0.0
        self.prev_sp = None
        self.load_mean = 0.0
        self.load_var = 0.0
        self.load_n = 0
        self.n = 0

    def _reset_history(self) -> None:
        # Carry only the current limit; forget every pre-change observation.
        self.anchor = self.limit
        self.integ = 0.0
        self.ewma_err = 0.0
        self.load_mean = 0.0
        self.load_var = 0.0
        self.load_n = 0

    def tick(self, obs: dict) -> dict:
        sp = float(obs["setpoint_ms"])
        lat = float(obs["latency_ms"])
        arr = float(obs["arrival_rps"])
        sp_changed = self.prev_sp is not None and sp != self.prev_sp
        if sp_changed:
            self._reset_history()
        self.prev_sp = sp

        # Volatility from past observations only (this tick's error is judged
        # against a setpoint that does not depend on this tick).
        if self.load_n >= 2 and self.load_mean > 0.0:
            cv = (self.load_var ** 0.5) / self.load_mean
        else:
            cv = 0.0
        cut = min(VOL_GAIN * cv, VOL_MAX_CUT)
        sp_eff = sp * (1.0 - cut)

        e = (sp_eff - lat) / sp_eff if sp_eff > 0.0 else 0.0
        e = min(max(e, -ERR_CLIP), ERR_CLIP)
        fe = _shape(e)

        self.ewma_err += ERR_ALPHA * (fe - self.ewma_err)
        self.integ += KI_UP * fe
        prop = KP_UP * self.ewma_err
        # Offset from the anchor, quantized independently of the anchor, so the
        # correction sequence after a reset is independent of the prior level.
        offset = round((self.integ + prop) * GRID) / GRID
        raw = self.anchor + offset
        out = min(max(raw, self.min_limit), self.max_limit)
        saturated = 0
        if out != raw:
            saturated = 1 if raw > out else -1
            self.integ = (out - self.anchor) - prop  # back-calculation anti-windup
        correction = out - self.limit
        self.limit = out

        # Update load statistics after using them.
        if self.load_n == 0:
            self.load_mean = arr
            self.load_var = 0.0
        else:
            d = arr - self.load_mean
            self.load_mean += VOL_ALPHA * d
            self.load_var = (1.0 - VOL_ALPHA) * (self.load_var + VOL_ALPHA * d * d)
        self.load_n += 1
        self.n += 1

        telemetry = {
            "error": e,
            "shaped_error": fe,
            "ewma_error": self.ewma_err,
            "integral": self.integ,
            "proportional": prop,
            "correction": correction,
            "anchor_limit": self.anchor,
            "effective_setpoint_ms": sp_eff,
            "load_cv": cv,
            "setpoint_cut": cut,
            "saturated": saturated,
            "setpoint_reset": 1 if sp_changed else 0,
            "ticks": self.n,
        }
        return {"limit": out, "telemetry": telemetry}

    def snapshot(self) -> str:
        return json.dumps({k: getattr(self, k) for k in _STATE_KEYS}, sort_keys=True)

    @classmethod
    def restore(cls, blob: str) -> "Controller":
        state = json.loads(blob)
        obj = cls.__new__(cls)
        for k in _STATE_KEYS:
            setattr(obj, k, state[k])
        return obj
