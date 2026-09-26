import json
import math

_EPS = 1e-9

# Tuning constants
_KD = 0.25          # downward gain (log-limit per unit normalized error)
_KU = 0.05          # upward gain (5x weaker than downward)
_KT = 0.75          # weight of the recency-weighted trend term
_KV = 2.0           # volatility sensitivity
_ALPHA = 0.3        # EWMA weight for smoothed error (recent obs weigh more)
_BETA = 0.2         # EWMA weight for error variance
_E_MIN = -1.0
_E_MAX = 2.0
_DLOG_MIN = -0.35
_DLOG_MAX = 0.25


def _clip(x, lo, hi):
    return lo if x < lo else (hi if x > hi else x)


class Controller:
    def __init__(self, config: dict):
        config = config or {}
        self.min_limit = float(config.get("min_limit", 1.0))
        self.max_limit = float(config.get("max_limit", 200.0))
        self.initial_limit = float(config.get("initial_limit", 50.0))
        self.limit = _clip(self.initial_limit, self.min_limit, self.max_limit)
        self.setpoint = None    # setpoint the current state was built under
        self.smooth = None      # EWMA of normalized error (None => no history)
        self.var = 0.0          # EWMA variance of error innovations

    def tick(self, obs: dict) -> dict:
        sp = float(obs.get("setpoint_ms", 0.0))
        if not math.isfinite(sp) or sp < _EPS:
            sp = _EPS
        lat = float(obs.get("latency_ms", sp))
        if math.isfinite(lat):
            e = _clip((lat - sp) / sp, _E_MIN, _E_MAX)
        else:
            e = _E_MAX

        # Setpoint change: drop all pre-change history.
        reset = 0
        if self.setpoint is None or sp != self.setpoint:
            reset = 1
            self.setpoint = sp
            self.smooth = None
            self.var = 0.0

        s_prev = e if self.smooth is None else self.smooth
        dev = e - s_prev
        self.var = (1.0 - _BETA) * (self.var + _BETA * dev * dev)
        sd = math.sqrt(self.var)

        # Trend: how the newest observation departs from the recency-weighted
        # history; bounded by the magnitude of the current error.
        bound = abs(e)
        trend = _clip(dev, -bound, bound)
        e_eff = e + _KT * trend

        vol = 1.0 + _KV * sd
        kd = _KD * vol
        ku = _KU / vol
        if e_eff > 0.0:
            dlog = -kd * e_eff
        else:
            dlog = ku * (-e_eff)
        dlog = _clip(dlog, _DLOG_MIN, _DLOG_MAX)

        saturated = 0
        if (self.limit >= self.max_limit and dlog > 0.0) or \
           (self.limit <= self.min_limit and dlog < 0.0):
            saturated = 1
            # Anti-windup: no memory accumulates while pinned at a bound.
            self.smooth = e
        else:
            self.smooth = s_prev + _ALPHA * (e - s_prev)

        new_limit = self.limit * math.exp(dlog)
        new_limit = _clip(new_limit, self.min_limit, self.max_limit)
        self.limit = new_limit

        telemetry = {
            "error": float(e),
            "smoothed_error": float(self.smooth),
            "trend": float(trend),
            "effective_error": float(e_eff),
            "volatility": float(sd),
            "gain_down": float(kd),
            "gain_up": float(ku),
            "log_correction": float(dlog),
            "saturated": int(saturated),
            "setpoint_reset": int(reset),
        }
        return {"limit": float(new_limit), "telemetry": telemetry}

    def snapshot(self) -> str:
        state = {
            "min_limit": self.min_limit,
            "max_limit": self.max_limit,
            "initial_limit": self.initial_limit,
            "limit": self.limit,
            "setpoint": self.setpoint,
            "smooth": self.smooth,
            "var": self.var,
        }
        return json.dumps(state, sort_keys=True)

    @classmethod
    def restore(cls, blob: str) -> "Controller":
        st = json.loads(blob)
        c = cls({
            "min_limit": st["min_limit"],
            "max_limit": st["max_limit"],
            "initial_limit": st["initial_limit"],
        })
        c.limit = float(st["limit"])
        c.setpoint = None if st["setpoint"] is None else float(st["setpoint"])
        c.smooth = None if st["smooth"] is None else float(st["smooth"])
        c.var = float(st["var"])
        return c
