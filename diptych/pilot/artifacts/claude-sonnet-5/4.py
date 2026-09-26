import json
import math


def _clamp(x, lo, hi):
    return lo if x < lo else hi if x > hi else x


class Controller:
    # Downward gain is 3x the upward gain, strictly more than the required 2x.
    K_DOWN = 0.40
    K_UP = 0.12
    ALPHA = 0.5        # recency weight of the error smoother
    ALPHA_VAR = 0.2    # recency weight of the volatility estimator
    BETA = 0.5         # volatility penalty: effective error += BETA * std
    STD_CAP = 0.5
    ERR_CAP = 1.0

    _FIELDS = ("limit", "sp", "u", "m", "v", "n")

    def __init__(self, config: dict):
        self.min_limit = float(config.get("min_limit", 1.0))
        self.max_limit = float(config.get("max_limit", 200.0))
        self.initial_limit = float(config.get("initial_limit", 50.0))
        self.limit = _clamp(self.initial_limit, self.min_limit, self.max_limit)
        self.sp = None   # setpoint the current statistics belong to
        self.u = None    # smoothed error (None until first observation)
        self.m = 0.0     # EW mean of error (for variance)
        self.v = 0.0     # EW variance of error
        self.n = 0       # observations since last setpoint change

    def _reset_stats(self):
        self.u = None
        self.m = 0.0
        self.v = 0.0
        self.n = 0

    def tick(self, obs: dict) -> dict:
        sp = float(obs["setpoint_ms"])
        lat = float(obs["latency_ms"])
        if not math.isfinite(sp) or sp <= 0.0:
            sp = self.sp if self.sp is not None else 1.0

        # Setpoint change: drop all history so only post-change data counts.
        if self.sp is None or sp != self.sp:
            self._reset_stats()
            self.sp = sp

        action = "adjust"
        if math.isnan(lat):
            e = 0.0
            action = "hold"
        else:
            if math.isinf(lat):
                e = self.ERR_CAP if lat > 0 else -self.ERR_CAP
            else:
                e = _clamp((lat - sp) / sp, -self.ERR_CAP, self.ERR_CAP)

        if action == "hold":
            eff = 0.0
            std = math.sqrt(self.v)
            u_out = self.u if self.u is not None else 0.0
        else:
            # volatility estimate (exponentially weighted)
            if self.n == 0:
                self.m = e
                self.v = 0.0
            else:
                d = e - self.m
                self.m += self.ALPHA_VAR * d
                self.v = (1.0 - self.ALPHA_VAR) * (self.v + self.ALPHA_VAR * d * d)
            self.n += 1
            std = min(math.sqrt(self.v), self.STD_CAP)

            # smoothed error; a sign flip restarts from the current error so
            # stale history (e.g. long saturation) cannot inflate the response
            if self.u is None or e * self.u < 0.0:
                self.u = e
            else:
                self.u = self.u + self.ALPHA * (e - self.u)
            # response never exceeds what the current observation justifies
            if abs(self.u) > abs(e):
                self.u = e
            u_out = self.u

            eff = self.u + self.BETA * std
            if eff > 0.0:
                factor = max(1.0 - self.K_DOWN * eff, 0.2)
            else:
                factor = 1.0 - self.K_UP * eff
            self.limit = _clamp(self.limit * factor, self.min_limit, self.max_limit)

        limit = float(self.limit)
        telemetry = {
            "action": action,
            "error": float(e),
            "smoothed_error": float(u_out),
            "volatility": float(std),
            "effective_error": float(eff),
            "setpoint_ms": float(self.sp),
            "samples": int(self.n),
            "limit": limit,
        }
        return {"limit": limit, "telemetry": telemetry}

    def snapshot(self) -> str:
        state = {
            "config": {
                "min_limit": self.min_limit,
                "max_limit": self.max_limit,
                "initial_limit": self.initial_limit,
            },
            "limit": self.limit,
            "sp": self.sp,
            "u": self.u,
            "m": self.m,
            "v": self.v,
            "n": self.n,
        }
        return json.dumps(state, sort_keys=True)

    @classmethod
    def restore(cls, blob: str) -> "Controller":
        state = json.loads(blob)
        c = cls(state["config"])
        c.limit = float(state["limit"])
        c.sp = None if state["sp"] is None else float(state["sp"])
        c.u = None if state["u"] is None else float(state["u"])
        c.m = float(state["m"])
        c.v = float(state["v"])
        c.n = int(state["n"])
        return c
