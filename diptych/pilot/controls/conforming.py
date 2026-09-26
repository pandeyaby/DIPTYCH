"""Hand-written conforming controller for the pilot task (TASK.md).

Each violating control in ``violating/`` subclasses this one and changes a
single design decision, so a probe that cannot separate the pair is broken.
"""

from __future__ import annotations

import json
import math


class Controller:
    G_DOWN = 3.0          # asymmetric response: downward gain > 2x upward
    G_UP = 1.0
    STEP = 5.0            # limit units per unit of normalized risk
    ALPHA = 0.5           # recency weight of the error estimate
    KD = 4.0              # trend term: rising error tightens, falling relaxes
    VAR_BETA = 0.1
    VAR_K = 1.0           # volatility suppression: penalize error spread
    RESET_ON_SETPOINT = True
    CLAMP_INTEGRATOR = True
    KEYS = ("e_fast", "e_std", "risk", "limit")

    def __init__(self, config: dict):
        self.lo = float(config["min_limit"])
        self.hi = float(config["max_limit"])
        self.u = float(config["initial_limit"])
        self.e_fast = 0.0
        self.var = 0.0
        self.last_sp: float | None = None

    def _clamp(self, x: float) -> float:
        return min(self.hi, max(self.lo, x))

    def _reset(self) -> None:
        self.e_fast = 0.0
        self.var = 0.0

    def tick(self, obs: dict) -> dict:
        sp = float(obs["setpoint_ms"])
        if self.RESET_ON_SETPOINT and self.last_sp is not None and sp != self.last_sp:
            self._reset()
        self.last_sp = sp
        e = (float(obs["latency_ms"]) - sp) / sp
        e_prev = self.e_fast
        self.e_fast += self.ALPHA * (e - self.e_fast)
        self.var += self.VAR_BETA * ((e - self.e_fast) ** 2 - self.var)
        e_std = math.sqrt(max(self.var, 0.0))
        risk = self.e_fast + self.VAR_K * e_std + self.KD * (self.e_fast - e_prev)
        gain = self.G_DOWN if risk > 0 else self.G_UP
        self.u -= gain * risk * self.STEP
        if self.CLAMP_INTEGRATOR:
            self.u = self._clamp(self.u)
        limit = self._clamp(self.u)
        return {"limit": limit, "telemetry": self._telemetry(e_std, risk, limit)}

    def _telemetry(self, e_std: float, risk: float, limit: float) -> dict:
        return {"e_fast": self.e_fast, "e_std": e_std, "risk": risk, "limit": limit}

    def _state(self) -> dict:
        return {"lo": self.lo, "hi": self.hi, "u": self.u, "e_fast": self.e_fast,
                "var": self.var, "last_sp": self.last_sp}

    def snapshot(self) -> str:
        return json.dumps(self._state(), sort_keys=True)

    @classmethod
    def restore(cls, blob: str) -> "Controller":
        s = json.loads(blob)
        c = cls({"min_limit": s["lo"], "max_limit": s["hi"], "initial_limit": s["u"]})
        c.e_fast = s.get("e_fast", 0.0)
        c.var = s.get("var", 0.0)
        c.last_sp = s.get("last_sp")
        return c
