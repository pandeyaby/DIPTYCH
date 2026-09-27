"""Adaptive concurrency controller: asymmetric, filtered integral control."""

import json

# Gains (limit units per unit of normalised latency error, per tick).
K_UP = 6.0            # latency below target -> raise limit
K_DOWN = 24.0         # latency above target -> lower limit (4x the upward gain)
TREND_GAIN = 1.5      # weight on (fast - slow) filtered error
ALPHA_FAST = 0.4      # recency-weighted error filter
ALPHA_SLOW = 0.1
ALPHA_VAR = 0.05      # arrival mean / variance filter
VOL_GAIN = 2.0        # setpoint shrink per unit coefficient of variation
ERR_CLIP = 1.0        # symmetric clip on normalised error

_FIELDS = ("min_limit", "max_limit", "limit", "sp", "es", "el", "mean_a", "var_a", "n")


class Controller:
    def __init__(self, config):
        self.min_limit = float(config.get("min_limit", 1.0))
        self.max_limit = float(config.get("max_limit", 200.0))
        init = float(config.get("initial_limit", 50.0))
        self.limit = min(max(init, self.min_limit), self.max_limit)
        self._reset(None)

    def _reset(self, sp):
        # Drops all observation history (used at start and on setpoint change).
        self.sp = sp
        self.es = 0.0
        self.el = 0.0
        self.mean_a = 0.0
        self.var_a = 0.0
        self.n = 0

    def tick(self, obs):
        sp = float(obs["setpoint_ms"])
        lat = float(obs["latency_ms"])
        arr = float(obs["arrival_rps"])

        if self.sp is None or sp != self.sp:
            self._reset(sp)

        # Arrival volatility (coefficient of variation), independent of the limit.
        if self.n == 0:
            self.mean_a = arr
            self.var_a = 0.0
        else:
            d = arr - self.mean_a
            self.mean_a += ALPHA_VAR * d
            self.var_a = (1.0 - ALPHA_VAR) * (self.var_a + ALPHA_VAR * d * d)
        cv = (self.var_a ** 0.5) / self.mean_a if self.mean_a > 1e-9 else 0.0

        # Normalised error against a volatility-shrunk target.
        e = (lat - sp * (1.0 - VOL_GAIN * cv)) / sp
        e = min(max(e, -ERR_CLIP), ERR_CLIP)

        if self.n == 0:
            self.es = e
            self.el = e
        else:
            self.es += ALPHA_FAST * (e - self.es)
            self.el += ALPHA_SLOW * (e - self.el)
        self.n += 1

        drive = self.es + TREND_GAIN * (self.es - self.el)  # >0: too slow / worsening
        gain = K_DOWN if drive > 0.0 else K_UP
        # Clamping the state itself (not an unbounded accumulator) prevents windup.
        self.limit = min(max(self.limit - gain * drive, self.min_limit), self.max_limit)

        return {
            "limit": self.limit,
            "telemetry": {
                "err": e,
                "err_fast": self.es,
                "err_slow": self.el,
                "drive": drive,
                "arrival_cv": cv,
                "n_since_reset": self.n,
                "phase": "up" if drive <= 0.0 else "down",
            },
        }

    def snapshot(self):
        return json.dumps({k: getattr(self, k) for k in _FIELDS}, sort_keys=True)

    @classmethod
    def restore(cls, blob):
        d = json.loads(blob)
        c = cls({"min_limit": d["min_limit"], "max_limit": d["max_limit"], "initial_limit": d["limit"]})
        for k in _FIELDS:
            setattr(c, k, d[k])
        return c
