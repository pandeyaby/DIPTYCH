import json
import math

_DN_GAIN = 0.25       # per-tick log-limit correction per unit of positive error
_UP_GAIN = 0.06       # per-tick log-limit correction per unit of negative error
_TREND_GAIN = 1.0     # weight of the recency-smoothed error in the output
_SMOOTH_ALPHA = 0.4   # EWMA weight for the recent-trend estimate
_VAR_ALPHA = 0.1      # EWMA weight for volatility estimation
_RISK_K = 1.0         # std multiples added to error (volatility suppression)
_ERR_LO = -1.0
_ERR_HI = 4.0


def _clip(x, lo, hi):
    return lo if x < lo else hi if x > hi else x


def _shape(x):
    """Asymmetric gain: downward push is much stronger than upward."""
    return _DN_GAIN * x if x > 0.0 else _UP_GAIN * x


class Controller:
    def __init__(self, config: dict):
        self.min_limit = float(config.get("min_limit", 1.0))
        self.max_limit = float(config.get("max_limit", 200.0))
        self.initial_limit = float(config.get("initial_limit", 50.0))
        self._integral = _clip(self.initial_limit, self.min_limit, self.max_limit)
        self._setpoint = None
        self._smooth = 0.0
        self._mean = 0.0
        self._var = 0.0
        self._limit = self._integral

    def tick(self, obs: dict) -> dict:
        sp = float(obs["setpoint_ms"])
        lat = float(obs["latency_ms"])
        changed = 0
        if self._setpoint is None or sp != self._setpoint:
            changed = 1
            fresh = True
            self._setpoint = sp
        else:
            fresh = False

        if lat != lat:
            e = 0.0
        elif sp > 0.0:
            e = _clip((lat - sp) / sp, _ERR_LO, _ERR_HI)
        else:
            e = _ERR_HI if lat > 0.0 else 0.0

        if fresh:
            # Discard all pre-change history; start from this observation.
            self._smooth = e
            self._mean = e
            self._var = 0.0
        else:
            self._smooth += _SMOOTH_ALPHA * (e - self._smooth)
            d = e - self._mean
            self._mean += _VAR_ALPHA * d
            self._var = (1.0 - _VAR_ALPHA) * (self._var + _VAR_ALPHA * d * d)

        std = math.sqrt(self._var)
        risk = _RISK_K * std
        e_eff = _clip(e + risk, _ERR_LO, _ERR_HI + risk)
        s_eff = self._smooth + risk

        # Integral state lives in [min, max]: it can never wind up past the
        # bounds, so the first move away from saturation depends only on the
        # currently observed error.
        self._integral = _clip(
            self._integral * math.exp(-_shape(e_eff)),
            self.min_limit, self.max_limit)

        # Non-accumulating, recency-weighted trend term.
        out = self._integral * math.exp(-_TREND_GAIN * _shape(s_eff))
        out = _clip(out, self.min_limit, self.max_limit)
        self._limit = out

        telemetry = {
            "t": int(obs.get("t", 0)),
            "error": float(e),
            "smoothed_error": float(self._smooth),
            "std": float(std),
            "effective_error": float(e_eff),
            "integral": float(self._integral),
            "setpoint_changed": int(changed),
            "limit": float(out),
        }
        return {"limit": float(out), "telemetry": telemetry}

    def snapshot(self) -> str:
        return json.dumps({
            "min_limit": self.min_limit,
            "max_limit": self.max_limit,
            "initial_limit": self.initial_limit,
            "integral": self._integral,
            "setpoint": self._setpoint,
            "smooth": self._smooth,
            "mean": self._mean,
            "var": self._var,
            "limit": self._limit,
        }, sort_keys=True)

    @classmethod
    def restore(cls, blob: str) -> "Controller":
        d = json.loads(blob)
        c = cls({
            "min_limit": d["min_limit"],
            "max_limit": d["max_limit"],
            "initial_limit": d["initial_limit"],
        })
        c._integral = float(d["integral"])
        c._setpoint = None if d["setpoint"] is None else float(d["setpoint"])
        c._smooth = float(d["smooth"])
        c._mean = float(d["mean"])
        c._var = float(d["var"])
        c._limit = float(d["limit"])
        return c
