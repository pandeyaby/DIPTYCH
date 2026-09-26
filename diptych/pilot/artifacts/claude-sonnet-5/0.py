import json
import math

_A_FAST = 0.6     # fast EWMA weight on the newest error
_A_SLOW = 0.15    # slow EWMA weight, also used for the variance estimate
_KD = 0.5         # weight on the fast-minus-slow trend term
_KV = 0.5         # volatility penalty per unit of error std-dev
_UP = 0.05        # per-tick gain when latency is below setpoint
_DOWN = 0.25      # per-tick gain when latency is above setpoint (> 2x _UP)
_CLIP = 1.0       # bound on the normalised error and on the control signal

_STATE_KEYS = (
    "limit", "setpoint", "ticks", "n",
    "f", "pf", "s", "ps", "q",
)


def _clip(x, lo, hi):
    return lo if x < lo else hi if x > hi else x


class Controller:
    def __init__(self, config: dict):
        self.min_limit = float(config.get("min_limit", 1.0))
        self.max_limit = float(config.get("max_limit", 200.0))
        init = float(config.get("initial_limit", 50.0))
        self.limit = _clip(init, self.min_limit, self.max_limit)
        self.setpoint = None
        self.ticks = 0
        self._reset_filters()

    def _reset_filters(self):
        # Debiased EWMA state: raw value plus the decay power used to debias it.
        self.n = 0
        self.f = 0.0
        self.pf = 1.0
        self.s = 0.0
        self.ps = 1.0
        self.q = 0.0

    def tick(self, obs: dict) -> dict:
        sp = float(obs.get("setpoint_ms", 0.0))
        lat = float(obs.get("latency_ms", 0.0))

        # A new setpoint discards all history gathered against the old one.
        if self.setpoint is None or sp != self.setpoint:
            self._reset_filters()
            self.setpoint = sp

        if sp > 0.0 and math.isfinite(sp) and math.isfinite(lat):
            e = _clip((sp - lat) / sp, -_CLIP, _CLIP)
        else:
            e = 0.0

        # Update filters.
        self.n += 1
        self.f = (1.0 - _A_FAST) * self.f + _A_FAST * e
        self.pf *= (1.0 - _A_FAST)
        self.s = (1.0 - _A_SLOW) * self.s + _A_SLOW * e
        self.q = (1.0 - _A_SLOW) * self.q + _A_SLOW * e * e
        self.ps *= (1.0 - _A_SLOW)

        f_hat = self.f / (1.0 - self.pf)
        s_hat = self.s / (1.0 - self.ps)
        q_hat = self.q / (1.0 - self.ps)
        var = max(0.0, q_hat - s_hat * s_hat)
        sd = math.sqrt(var)
        bias = _KV * sd

        trend = f_hat - s_hat
        z = _clip(f_hat + _KD * trend - bias, -_CLIP, _CLIP)
        gain = _UP * z if z > 0.0 else _DOWN * z

        raw = self.limit * (1.0 + gain)
        new = _clip(raw, self.min_limit, self.max_limit)

        # Anti-windup: if the controller is pushing into a bound, discard the
        # accumulated filter memory so saturation time leaves no trace.
        saturated = 0
        if raw > self.max_limit or raw < self.min_limit:
            saturated = 1
            self._reset_filters()

        self.limit = new
        self.ticks += 1

        telemetry = {
            "error": float(e),
            "fast": float(f_hat),
            "slow": float(s_hat),
            "trend": float(trend),
            "std": float(sd),
            "bias": float(bias),
            "signal": float(z),
            "gain": float(gain),
            "raw_limit": float(raw),
            "saturated": saturated,
            "setpoint_ms": float(sp),
            "ticks": int(self.ticks),
        }
        return {"limit": float(new), "telemetry": telemetry}

    def snapshot(self) -> str:
        state = {k: getattr(self, k) for k in _STATE_KEYS}
        state["min_limit"] = self.min_limit
        state["max_limit"] = self.max_limit
        return json.dumps(state, sort_keys=True)

    @classmethod
    def restore(cls, blob: str) -> "Controller":
        state = json.loads(blob)
        obj = cls.__new__(cls)
        obj.min_limit = state["min_limit"]
        obj.max_limit = state["max_limit"]
        for k in _STATE_KEYS:
            setattr(obj, k, state[k])
        return obj
