"""Adaptive concurrency controller.

The controller has an additive integrator ("base") with asymmetric gains, so it
cuts the limit harder than it raises it. On top of that it adds:
  * a variance penalty that pushes the limit down harder when latency is noisy,
  * a weak leak toward an anchor, so recent corrections count for more,
  * an asymmetric proportional term on a recency-weighted (EWMA) error.

Anti-windup: the integrator is clamped to [min_limit, max_limit]. While the
limit is pinned at max_limit and latency is still under the setpoint, the EWMA
is cleared and the variance is frozen. That way the first response to an
excursion depends only on the error just observed.

Setpoint change: the integrator is set to the last limit returned and the anchor
moves there too. The EWMA and variance are cleared. Steps are additive and
relative to the anchor, so the corrections that follow depend only on
observations made after the change.
"""

import json
import math


_STATE_VERSION = 1

# Gains (dimensionless; multiplied by the scale S, which is in limit units).
_KU = 0.12      # integrator gain, upward (latency below setpoint)
_KD = 0.40      # integrator gain, downward (latency above setpoint); > 3x _KU
_KPU = 0.08     # proportional gain on EWMA error, upward
_KPD = 0.30     # proportional gain on EWMA error, downward; > 3x _KPU
_KV = 0.10      # volatility penalty gain (per unit std of normalized error)
_ALPHA = 0.30   # EWMA weight of the newest error
_ALPHA_V = 0.10  # EWMA weight for the variance estimate
_LEAK = 0.002   # leak of the integrator toward its anchor
_E_MIN = -1.0   # clip bounds for normalized error
_E_MAX = 2.0


def _finite(x):
    return isinstance(x, (int, float)) and math.isfinite(float(x))


class Controller:
    _FIELDS = (
        "min_limit", "max_limit", "initial_limit", "scale",
        "base", "anchor", "ewma", "var", "prev_sp", "last_limit",
        "ticks", "since_change", "changes",
    )

    def __init__(self, config: dict):
        mn = float(config.get("min_limit", 1.0))
        mx = float(config.get("max_limit", 200.0))
        if mx < mn:
            mn, mx = mx, mn
        init = float(config.get("initial_limit", 50.0))
        init = min(max(init, mn), mx)
        self.min_limit = mn
        self.max_limit = mx
        self.initial_limit = init
        self.scale = max(0.25 * (mx - mn), 1e-9)
        self.base = init
        self.anchor = init
        self.ewma = 0.0
        self.var = 0.0
        self.prev_sp = None
        self.last_limit = init
        self.ticks = 0
        self.since_change = 0
        self.changes = 0

    def _clamp(self, x):
        if x < self.min_limit:
            return self.min_limit
        if x > self.max_limit:
            return self.max_limit
        return x

    def tick(self, obs: dict) -> dict:
        lat = obs.get("latency_ms", 0.0)
        sp = obs.get("setpoint_ms", 0.0)
        lat = float(lat) if _finite(lat) else float("nan")
        sp = float(sp) if _finite(sp) else float("nan")

        # Setpoint change: drop the history from before the change.
        sp_valid = math.isfinite(sp) and sp > 0.0
        if sp_valid:
            if self.prev_sp is not None and sp != self.prev_sp:
                self.base = self.last_limit
                self.anchor = self.base
                self.ewma = 0.0
                self.var = 0.0
                self.since_change = 0
                self.changes += 1
            self.prev_sp = sp

        # Normalized error (positive means latency above setpoint).
        if sp_valid and math.isfinite(lat):
            e = (lat - sp) / sp
            if e < _E_MIN:
                e = _E_MIN
            elif e > _E_MAX:
                e = _E_MAX
        else:
            e = 0.0

        old_var = self.var
        dev = e - self.ewma
        self.var = (1.0 - _ALPHA_V) * self.var + _ALPHA_V * dev * dev
        self.ewma = (1.0 - _ALPHA) * self.ewma + _ALPHA * e
        std = math.sqrt(self.var) if self.var > 0.0 else 0.0

        s = self.scale
        if e > 0.0:
            step = -_KD * s * e
        else:
            step = -_KU * s * e
        vol = _KV * s * std
        step -= vol
        leak = _LEAK * (self.anchor - self.base)
        self.base = self._clamp(self.base + step + leak)

        # Anti-windup: pinned at the top with no excursion means no memory builds up.
        saturated = 1 if (self.base >= self.max_limit and e <= 0.0) else 0
        if saturated:
            self.ewma = 0.0
            self.var = old_var
            std = math.sqrt(self.var) if self.var > 0.0 else 0.0

        if self.ewma > 0.0:
            p_term = -_KPD * s * self.ewma
        else:
            p_term = -_KPU * s * self.ewma

        limit = self._clamp(self.base + p_term)
        self.last_limit = limit
        self.ticks += 1
        self.since_change += 1

        telemetry = {
            "tick": int(self.ticks),
            "error": float(e),
            "ewma_error": float(self.ewma),
            "std_error": float(std),
            "integrator": float(self.base),
            "anchor": float(self.anchor),
            "step": float(step),
            "volatility_penalty": float(vol),
            "leak": float(leak),
            "p_term": float(p_term),
            "saturated": int(saturated),
            "ticks_since_setpoint_change": int(self.since_change),
            "setpoint_changes": int(self.changes),
            "setpoint_ms": float(self.prev_sp) if self.prev_sp is not None else 0.0,
        }
        return {"limit": float(limit), "telemetry": telemetry}

    def snapshot(self) -> str:
        state = {"v": _STATE_VERSION}
        for k in self._FIELDS:
            val = getattr(self, k)
            if isinstance(val, float):
                state[k] = ["f", val.hex()]
            elif val is None:
                state[k] = ["n", None]
            else:
                state[k] = ["i", int(val)]
        return json.dumps(state, sort_keys=True, separators=(",", ":"))

    @classmethod
    def restore(cls, blob: str) -> "Controller":
        state = json.loads(blob)
        obj = cls.__new__(cls)
        for k in cls._FIELDS:
            kind, val = state[k]
            if kind == "f":
                setattr(obj, k, float.fromhex(val))
            elif kind == "n":
                setattr(obj, k, None)
            else:
                setattr(obj, k, int(val))
        return obj
