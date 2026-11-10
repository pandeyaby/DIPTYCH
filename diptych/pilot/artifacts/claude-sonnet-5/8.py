"""Adaptive concurrency controller.

Tracks a latency setpoint via a recency-weighted (EWMA) estimate of the
relative latency error, with an asymmetric multiplicative response (faster
to back off than to grow) and a volatility penalty that keeps the limit more
conservative under noisy conditions.
"""

import json


class Controller:
    # Fixed algorithm constants (not user-configurable; keeps behavior
    # deterministic and schema-stable across runs/processes).
    _ALPHA = 0.25        # EWMA decay: higher = more weight on recent obs.
    _K_DOWN = 0.35        # Downward (latency too high) gain.
    _K_UP = 0.10          # Upward (latency has headroom) gain.
    _K_VOL = 2.0          # Volatility penalty strength.
    _ERROR_CAP = 2.0      # Clamp on |relative error| used for the gain term.

    def __init__(self, config: dict):
        self.min_limit = float(config.get("min_limit", 1.0))
        self.max_limit = float(config.get("max_limit", 200.0))
        initial_limit = float(config.get("initial_limit", 50.0))
        self.limit = max(self.min_limit, min(self.max_limit, initial_limit))

        self.mean_ewma = 0.0
        self.var_ewma = 0.0
        self.last_setpoint = None
        self.ticks_since_reset = 0

    def tick(self, obs: dict) -> dict:
        t = obs["t"]
        latency = float(obs["latency_ms"])
        setpoint = float(obs["setpoint_ms"])

        if setpoint > 0:
            rel_error = (latency - setpoint) / setpoint
        else:
            rel_error = latency - setpoint

        reset = self.last_setpoint is None or setpoint != self.last_setpoint
        if reset:
            # Wipe history so corrections depend only on post-change obs,
            # and so a long prior saturation never inflates this response
            # (anti-windup: no term here grows with elapsed time).
            self.mean_ewma = rel_error
            self.var_ewma = 0.0
            self.ticks_since_reset = 0
        else:
            diff = rel_error - self.mean_ewma
            incr = self._ALPHA * diff
            self.mean_ewma = self.mean_ewma + incr
            # Standard EWMA variance update (Welford-style, exponential form).
            self.var_ewma = (1.0 - self._ALPHA) * (self.var_ewma + diff * incr)
            self.ticks_since_reset += 1

        self.last_setpoint = setpoint

        e = self.mean_ewma
        if e > 0.0:
            d = min(e, self._ERROR_CAP)
            down_comp = min(self._K_DOWN * d, 0.9)
            factor = 1.0 - down_comp
        else:
            u = min(-e, self._ERROR_CAP)
            up_comp = min(self._K_UP * u, 1.0)
            factor = 1.0 + up_comp

        vol_penalty = 1.0 / (1.0 + self._K_VOL * self.var_ewma)

        new_limit = self.limit * factor * vol_penalty
        new_limit = max(self.min_limit, min(self.max_limit, new_limit))
        self.limit = new_limit

        saturated = 1 if (
            self.limit >= self.max_limit - 1e-9 or self.limit <= self.min_limit + 1e-9
        ) else 0

        telemetry = {
            "tick": int(t),
            "limit": float(self.limit),
            "rel_error": float(rel_error),
            "mean_ewma": float(self.mean_ewma),
            "var_ewma": float(self.var_ewma),
            "factor": float(factor),
            "vol_penalty": float(vol_penalty),
            "saturated": int(saturated),
            "reset": int(1 if reset else 0),
            "ticks_since_reset": int(self.ticks_since_reset),
        }

        return {"limit": float(self.limit), "telemetry": telemetry}

    def snapshot(self) -> str:
        state = {
            "min_limit": self.min_limit,
            "max_limit": self.max_limit,
            "limit": self.limit,
            "mean_ewma": self.mean_ewma,
            "var_ewma": self.var_ewma,
            "last_setpoint": self.last_setpoint,
            "ticks_since_reset": self.ticks_since_reset,
        }
        return json.dumps(state, sort_keys=True)

    @classmethod
    def restore(cls, blob: str) -> "Controller":
        state = json.loads(blob)
        obj = cls.__new__(cls)
        obj.min_limit = state["min_limit"]
        obj.max_limit = state["max_limit"]
        obj.limit = state["limit"]
        obj.mean_ewma = state["mean_ewma"]
        obj.var_ewma = state["var_ewma"]
        obj.last_setpoint = state["last_setpoint"]
        obj.ticks_since_reset = state["ticks_since_reset"]
        return obj
