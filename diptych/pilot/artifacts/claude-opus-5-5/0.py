"""Adaptive concurrency controller.

The limit is adjusted multiplicatively in log space:

    log(limit) += -gain(u) * u

    u = e + K_DERIV * (e - e_prev) + K_VOL * sd(e)

where e is the relative latency error, (latency - setpoint) / setpoint.

- The gain depends on the sign of u. The downward gain is much larger than
  the upward gain, so a limit decrease is always more than twice the matching
  increase (requirement 1).
- The derivative term telescopes over a run of observations. That rewards
  improving trends and penalizes worsening ones, so recent observations
  dominate (requirement 2).
- Nothing integrates the error, so there is nothing to wind up. Each
  correction depends only on the current error, the previous error and a
  bounded volatility estimate (requirement 3).
- The volatility estimate (the EWMA standard deviation of e) raises u. A
  noisier workload therefore settles at a lower limit (requirement 4).
- A change of setpoint clears all error history (requirement 5).
- Only deterministic float arithmetic is used, and the state is serialised
  with exact float repr (requirements 6 and 7).
"""

import json
import math

_K_DOWN = 0.30      # log-gain applied when u > 0 (reduce the limit)
_K_UP = 0.06        # log-gain applied when u < 0 (raise the limit)
_K_DERIV = 0.5      # weight of the error trend
_K_VOL = 0.5        # weight of the error standard deviation
_U_CLIP = 1.5       # symmetric clip on the control signal
_E_CLIP = 4.0       # clip on the relative error
_VOL_ALPHA_MIN = 0.1

_TELEMETRY_KEYS = (
    "error", "error_prev", "deriv", "vol_mean", "vol_sd", "signal",
    "direction", "gain", "step_log", "setpoint_ms", "samples_since_reset",
    "setpoint_resets", "saturated", "limit",
)


def _finite(x, default):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return default
    if math.isnan(x) or math.isinf(x):
        return default
    return x


class Controller:
    def __init__(self, config: dict):
        self.min_limit = float(config.get("min_limit", 1.0))
        self.max_limit = float(config.get("max_limit", 200.0))
        if self.max_limit < self.min_limit:
            self.max_limit = self.min_limit
        init = float(config.get("initial_limit", 50.0))
        self.limit = min(self.max_limit, max(self.min_limit, init))
        self.setpoint = None     # last setpoint seen
        self.e_prev = None       # previous error since the last reset
        self.m = 0.0             # EWMA mean of the error
        self.v = 0.0             # EWMA variance of the error
        self.n = 0               # samples since the last reset
        self.resets = 0

    def _reset_history(self):
        self.e_prev = None
        self.m = 0.0
        self.v = 0.0
        self.n = 0

    def tick(self, obs: dict) -> dict:
        sp = _finite(obs.get("setpoint_ms"), self.setpoint if self.setpoint is not None else 1.0)
        if sp <= 0.0:
            sp = 1e-9
        if self.setpoint is None or sp != self.setpoint:
            if self.setpoint is not None:
                self.resets += 1
            self._reset_history()
            self.setpoint = sp

        lat = _finite(obs.get("latency_ms"), sp)
        if lat < 0.0:
            lat = 0.0
        e = (lat - sp) / sp
        if e > _E_CLIP:
            e = _E_CLIP
        elif e < -_E_CLIP:
            e = -_E_CLIP

        # Volatility: EWMA mean and variance of the error since the last reset.
        self.n += 1
        if self.n == 1:
            self.m = e
            self.v = 0.0
        else:
            a = max(_VOL_ALPHA_MIN, 1.0 / self.n)
            d = e - self.m
            self.m = self.m + a * d
            self.v = (1.0 - a) * (self.v + a * d * d)
        sd = math.sqrt(self.v) if self.v > 0.0 else 0.0

        deriv = 0.0 if self.e_prev is None else (e - self.e_prev)
        u = e + _K_DERIV * deriv + _K_VOL * sd
        if u > _U_CLIP:
            u = _U_CLIP
        elif u < -_U_CLIP:
            u = -_U_CLIP

        if u > 0.0:
            gain = _K_DOWN
            direction = "down"
        elif u < 0.0:
            gain = _K_UP
            direction = "up"
        else:
            gain = 0.0
            direction = "hold"
        step = -gain * u

        new_limit = self.limit * math.exp(step)
        if new_limit > self.max_limit:
            new_limit = self.max_limit
        elif new_limit < self.min_limit:
            new_limit = self.min_limit
        self.limit = new_limit

        e_prev_out = self.e_prev if self.e_prev is not None else 0.0
        self.e_prev = e

        saturated = 1 if new_limit >= self.max_limit else (-1 if new_limit <= self.min_limit else 0)
        telemetry = {
            "error": float(e),
            "error_prev": float(e_prev_out),
            "deriv": float(deriv),
            "vol_mean": float(self.m),
            "vol_sd": float(sd),
            "signal": float(u),
            "direction": direction,
            "gain": float(gain),
            "step_log": float(step),
            "setpoint_ms": float(sp),
            "samples_since_reset": int(self.n),
            "setpoint_resets": int(self.resets),
            "saturated": int(saturated),
            "limit": float(new_limit),
        }
        return {"limit": float(new_limit), "telemetry": telemetry}

    def snapshot(self) -> str:
        state = {
            "min_limit": self.min_limit,
            "max_limit": self.max_limit,
            "limit": self.limit,
            "setpoint": self.setpoint,
            "e_prev": self.e_prev,
            "m": self.m,
            "v": self.v,
            "n": self.n,
            "resets": self.resets,
        }
        return json.dumps(state, sort_keys=True)

    @classmethod
    def restore(cls, blob: str) -> "Controller":
        s = json.loads(blob)
        c = cls({
            "min_limit": s["min_limit"],
            "max_limit": s["max_limit"],
            "initial_limit": s["limit"],
        })
        c.limit = float(s["limit"])
        c.setpoint = None if s["setpoint"] is None else float(s["setpoint"])
        c.e_prev = None if s["e_prev"] is None else float(s["e_prev"])
        c.m = float(s["m"])
        c.v = float(s["v"])
        c.n = int(s["n"])
        c.resets = int(s["resets"])
        return c
