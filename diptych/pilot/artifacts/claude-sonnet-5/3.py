import json
import math

_KU = 0.10       # upward gain (per unit normalized error)
_KD = 0.40       # downward gain (4x upward, strictly > 2x)
_ALPHA_S = 0.5   # EWMA weight for the smoothed error (recency)
_C_TREND = 0.10  # gain on change of the smoothed error (recency-weighted trend)
_ALPHA_V = 0.10  # EWMA weight for latency mean/variance
_K_VOL = 0.5     # volatility penalty (in units of relative std)
_MAX_UP = 0.30   # bound on log-step up
_MAX_DOWN = 0.70  # bound on log-step down


class Controller:
    def __init__(self, config: dict):
        self.min_limit = float(config.get("min_limit", 1.0))
        self.max_limit = float(config.get("max_limit", 200.0))
        self.initial_limit = float(config.get("initial_limit", 50.0))
        self.limit = self._clamp(self.initial_limit)
        self._reset_filters(None)

    # ------------------------------------------------------------------
    def _clamp(self, x):
        if x != x:
            x = self.min_limit
        return min(self.max_limit, max(self.min_limit, x))

    def _reset_filters(self, setpoint):
        self.setpoint = setpoint
        self.s = None        # smoothed error
        self.mean = None     # EW mean of relative latency
        self.var = 0.0       # EW variance of relative latency
        self.n = 0           # ticks since last (re)set

    # ------------------------------------------------------------------
    def tick(self, obs: dict) -> dict:
        sp = float(obs["setpoint_ms"])
        if not (sp > 0.0) or sp == float("inf"):
            sp = 1.0
        lat = float(obs["latency_ms"])
        if lat != lat:
            lat = sp
        if lat == float("inf"):
            lat = sp * 1e6

        # Setpoint change: drop all history so corrections depend only on
        # post-change observations.
        if self.setpoint is None or sp != self.setpoint:
            self._reset_filters(sp)

        r = lat / sp
        if self.mean is None:
            self.mean = r
            self.var = 0.0
        else:
            dev = r - self.mean
            self.mean += _ALPHA_V * dev
            self.var = (1.0 - _ALPHA_V) * (self.var + _ALPHA_V * dev * dev)
        std = math.sqrt(self.var) if self.var > 0.0 else 0.0
        if std > 5.0:
            std = 5.0

        # Normalized error with volatility (risk) penalty; bounded so the
        # response is bounded by the immediately observed error.
        e = (1.0 - r) - _K_VOL * std
        e = max(-1.0, min(1.0, e))

        if self.s is None:
            s_new = e
            trend = 0.0
        else:
            s_new = self.s + _ALPHA_S * (e - self.s)
            trend = _C_TREND * (s_new - self.s)
        self.s = s_new
        self.n += 1

        base = _KU * e if e > 0.0 else _KD * e
        delta = base + trend
        delta = max(-_MAX_DOWN, min(_MAX_UP, delta))

        self.limit = self._clamp(self.limit * math.exp(delta))

        telemetry = {
            "limit": float(self.limit),
            "error": float(e),
            "smoothed_error": float(self.s),
            "trend": float(trend),
            "delta": float(delta),
            "latency_ratio_mean": float(self.mean),
            "latency_ratio_std": float(std),
            "setpoint_ms": float(sp),
            "ticks_since_reset": int(self.n),
        }
        return {"limit": float(self.limit), "telemetry": telemetry}

    # ------------------------------------------------------------------
    def snapshot(self) -> str:
        state = {
            "min_limit": self.min_limit,
            "max_limit": self.max_limit,
            "initial_limit": self.initial_limit,
            "limit": self.limit,
            "setpoint": self.setpoint,
            "s": self.s,
            "mean": self.mean,
            "var": self.var,
            "n": self.n,
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
        c.setpoint = st["setpoint"]
        c.s = st["s"]
        c.mean = st["mean"]
        c.var = float(st["var"])
        c.n = int(st["n"])
        return c
