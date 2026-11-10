"""Adaptive concurrency controller.

The limit is built from three parts:

* An integrator that sums asymmetric, error-proportional steps (shrinks fast,
  grows slowly). Sums commute, so the integrator does not depend on the order
  of the observations.
* A positional term driven by an EWMA of the error. It gives recent
  observations more weight, and it is asymmetric too.
* A volatility penalty proportional to the standard deviation of the error
  over a short window.

Anti-windup: the integrator is clamped to [min_limit, max_limit]. While the
integrator is pinned and the error pushes further into the bound, the EWMA is
held at zero, so the state stays the same however long saturation lasts.

Setpoint change: the last emitted limit is folded into the integrator. The
EWMA and the volatility window are cleared, so later corrections depend only
on observations made after the change.
"""

import json
import math

_WINDOW = 16          # volatility window length (power of two keeps means exact)
_ALPHA = 0.3          # EWMA weight for the newest error sample
_KI_DOWN = 0.04       # integrator gain when latency is above setpoint
_KI_UP = 0.01         # integrator gain when latency is below setpoint
_KP_DOWN = 0.08       # positional gain for positive smoothed error
_KP_UP = 0.02         # positional gain for negative smoothed error
_KV = 0.025           # volatility penalty gain
_E_LO = -1.0          # relative error clip bounds
_E_HI = 2.0

_TELEMETRY_KEYS = (
    "t", "error", "smoothed_error", "volatility", "integrator",
    "positional", "penalty", "limit", "setpoint_ms", "saturated",
    "setpoint_changed", "window_len", "admitted_ratio",
)


def _num(x, default):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return default
    return v if math.isfinite(v) else default


def _clamp(x, lo, hi):
    if x < lo:
        return lo
    if x > hi:
        return hi
    return x


class Controller:
    def __init__(self, config: dict):
        config = config or {}
        lo = _num(config.get("min_limit", 1.0), 1.0)
        hi = _num(config.get("max_limit", 200.0), 200.0)
        if hi < lo:
            lo, hi = hi, lo
        init = _clamp(_num(config.get("initial_limit", (lo + hi) / 2.0),
                           (lo + hi) / 2.0), lo, hi)
        self._lo = float(lo)
        self._hi = float(hi)
        self._span = float(hi - lo)
        self._integ = float(init)
        self._s = 0.0
        self._win = []
        self._sp = None
        self._last = float(init)

    # ------------------------------------------------------------------ core
    def tick(self, obs: dict) -> dict:
        obs = obs or {}
        try:
            t = int(obs.get("t", 0))
        except (TypeError, ValueError, OverflowError):
            t = 0

        # Setpoint handling and change detection.
        fallback_sp = self._sp if self._sp is not None else 1.0
        sp = _num(obs.get("setpoint_ms", fallback_sp), fallback_sp)
        if sp <= 0.0:
            sp = fallback_sp if fallback_sp > 0.0 else 1.0
        changed = self._sp is not None and sp != self._sp
        if changed:
            self._integ = self._last
            self._s = 0.0
            self._win = []
        self._sp = sp

        # Relative error, clipped.
        try:
            lat = float(obs.get("latency_ms", sp))
        except (TypeError, ValueError):
            lat = float("nan")
        if math.isnan(lat) or lat == math.inf:
            e = _E_HI
        elif lat == -math.inf:
            e = _E_LO
        else:
            e = _clamp((lat - sp) / sp, _E_LO, _E_HI)

        # Volatility over a short window.
        self._win.append(e)
        if len(self._win) > _WINDOW:
            del self._win[0:len(self._win) - _WINDOW]
        n = len(self._win)
        mean = math.fsum(self._win) / n
        var = math.fsum((x - mean) * (x - mean) for x in self._win) / n
        sd = math.sqrt(var) if var > 0.0 else 0.0

        # Asymmetric integrator, clamped (anti-windup).
        gain = _KI_DOWN if e > 0.0 else _KI_UP
        self._integ = _clamp(self._integ - self._span * gain * e,
                             self._lo, self._hi)

        # Recency-weighted positional term, held while pinned at a bound.
        saturated = "none"
        if self._integ >= self._hi and e <= 0.0:
            self._s = 0.0
            saturated = "max"
        elif self._integ <= self._lo and e >= 0.0:
            self._s = 0.0
            saturated = "min"
        else:
            self._s = (1.0 - _ALPHA) * self._s + _ALPHA * e

        kp = _KP_DOWN if self._s > 0.0 else _KP_UP
        pos = self._span * kp * self._s
        pen = self._span * _KV * sd

        out = float(_clamp(self._integ - pos - pen, self._lo, self._hi))
        self._last = out

        arrival = _num(obs.get("arrival_rps", 0.0), 0.0)
        admitted = _num(obs.get("admitted_rps", 0.0), 0.0)
        ratio = admitted / arrival if arrival > 0.0 else 1.0
        if not math.isfinite(ratio):
            ratio = 1.0

        telemetry = {
            "t": t,
            "error": float(e),
            "smoothed_error": float(self._s),
            "volatility": float(sd),
            "integrator": float(self._integ),
            "positional": float(pos),
            "penalty": float(pen),
            "limit": out,
            "setpoint_ms": float(sp),
            "saturated": saturated,
            "setpoint_changed": 1 if changed else 0,
            "window_len": n,
            "admitted_ratio": float(ratio),
        }
        return {"limit": out, "telemetry": telemetry}

    # ------------------------------------------------------------ persistence
    def snapshot(self) -> str:
        state = {
            "v": 1,
            "lo": self._lo.hex(),
            "hi": self._hi.hex(),
            "span": self._span.hex(),
            "integ": float(self._integ).hex(),
            "s": float(self._s).hex(),
            "win": [float(x).hex() for x in self._win],
            "sp": None if self._sp is None else float(self._sp).hex(),
            "last": float(self._last).hex(),
        }
        return json.dumps(state, sort_keys=True, separators=(",", ":"))

    @classmethod
    def restore(cls, blob: str) -> "Controller":
        d = json.loads(blob)
        obj = cls.__new__(cls)
        obj._lo = float.fromhex(d["lo"])
        obj._hi = float.fromhex(d["hi"])
        obj._span = float.fromhex(d["span"])
        obj._integ = float.fromhex(d["integ"])
        obj._s = float.fromhex(d["s"])
        obj._win = [float.fromhex(x) for x in d["win"]]
        obj._sp = None if d["sp"] is None else float.fromhex(d["sp"])
        obj._last = float.fromhex(d["last"])
        return obj
