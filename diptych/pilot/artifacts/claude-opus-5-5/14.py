"""Adaptive concurrency controller.

Each tick it computes an additive correction to the concurrency limit from
the normalised latency error e = (setpoint - latency) / setpoint:

    raw  = g(e) + k_trend * (e - e_prev) - k_vol * sigma
    step = clamp(raw, -cap * |e|, +cap * |e|)
    limit = clamp(limit + scale * step, min_limit, max_limit)

* g is asymmetric: errors that call for a decrease use a gain more than five
  times the gain used for increases.
* The trend term adds up to e_last - e_first over a run, so the same
  observations end at a higher limit when latency is improving than when it
  is worsening.
* sigma is an EWMA estimate of the error's standard deviation. A noisier
  workload gets a lower limit.
* Each step is capped in proportion to the error observed this tick, and the
  limit is clamped every tick, so nothing builds up while the limit sits at a
  bound (anti-windup).
* Corrections are additive and use a constant scale, so they do not depend on
  the current limit. When the setpoint changes, all error history (previous
  error, EWMA mean and variance) is cleared, so later corrections use only
  observations made after the change.
* All state is plain floats and ints. Snapshots are JSON, and float repr
  round-trips exactly, so restored controllers make bit-identical decisions.
"""

import json
import math

_K_UP = 0.08      # gain for latency below setpoint (raise limit)
_K_DOWN = 0.40    # gain for latency above setpoint (lower limit)
_K_TREND = 0.04   # gain on the one-tick change in error
_K_VOL = 0.10     # penalty per unit of error standard deviation
_CAP = 0.70       # max |step| per unit of |current error|
_ALPHA = 0.20     # EWMA factor for error mean / variance
_EPS = 1e-9

_STATE_KEYS = (
    "min_limit", "max_limit", "initial_limit", "scale", "limit",
    "setpoint", "prev_err", "mean_err", "var_err", "n_since_change",
    "n_ticks", "n_setpoint_changes",
)


class Controller:
    def __init__(self, config: dict):
        mn = float(config.get("min_limit", 1.0))
        mx = float(config.get("max_limit", 200.0))
        if mx < mn:
            mn, mx = mx, mn
        init = float(config.get("initial_limit", 50.0))
        if not math.isfinite(init):
            init = mn
        self.min_limit = mn
        self.max_limit = mx
        self.initial_limit = min(mx, max(mn, init))
        # Constant correction scale, in limit units per unit of step.
        self.scale = 0.25 * (mx - mn) if mx > mn else 0.0
        self.limit = self.initial_limit
        self.setpoint = None        # last seen setpoint (float) or None
        self.prev_err = None        # previous error since last setpoint change
        self.mean_err = None        # EWMA mean of error
        self.var_err = 0.0          # EWMA variance of error
        self.n_since_change = 0
        self.n_ticks = 0
        self.n_setpoint_changes = 0

    # ------------------------------------------------------------------ core
    def _reset_history(self):
        self.prev_err = None
        self.mean_err = None
        self.var_err = 0.0
        self.n_since_change = 0

    def tick(self, obs: dict) -> dict:
        sp = float(obs.get("setpoint_ms", 0.0))
        lat = float(obs.get("latency_ms", 0.0))
        arrival = float(obs.get("arrival_rps", 0.0))
        admitted = float(obs.get("admitted_rps", 0.0))

        sp_changed = 0
        if self.setpoint is None:
            self.setpoint = sp
        elif sp != self.setpoint:
            self.setpoint = sp
            self._reset_history()
            self.n_setpoint_changes += 1
            sp_changed = 1

        denom = sp if (math.isfinite(sp) and sp > _EPS) else _EPS
        if math.isfinite(lat):
            err = (denom - lat) / denom
        else:
            err = -1.0
        if not math.isfinite(err):
            err = -1.0
        # Bound pathological errors so a single sample cannot dominate.
        err = max(-10.0, min(1.0, err))

        # Volatility statistics (post-setpoint-change only).
        if self.mean_err is None:
            self.mean_err = err
            self.var_err = 0.0
        else:
            d = err - self.mean_err
            self.mean_err = self.mean_err + _ALPHA * d
            self.var_err = (1.0 - _ALPHA) * (self.var_err + _ALPHA * d * d)
        sigma = math.sqrt(self.var_err) if self.var_err > 0.0 else 0.0

        # Asymmetric proportional response.
        if err >= 0.0:
            prop = _K_UP * err
        else:
            prop = _K_DOWN * err

        # Trend (one-tick change in error).
        if self.prev_err is None:
            trend = 0.0
        else:
            trend = err - self.prev_err
        trend_term = _K_TREND * trend

        vol_term = _K_VOL * sigma
        raw = prop + trend_term - vol_term

        cap = _CAP * abs(err)
        step = max(-cap, min(cap, raw))

        new_limit = self.limit + self.scale * step
        if not math.isfinite(new_limit):
            new_limit = self.min_limit
        new_limit = min(self.max_limit, max(self.min_limit, new_limit))
        self.limit = float(new_limit)

        self.prev_err = err
        self.n_since_change += 1
        self.n_ticks += 1

        if self.limit >= self.max_limit:
            saturated = 1
        elif self.limit <= self.min_limit:
            saturated = -1
        else:
            saturated = 0

        if err < 0.0:
            regime = "decrease"
        elif err > 0.0:
            regime = "increase"
        else:
            regime = "hold"

        telemetry = {
            "error": float(err),
            "prop_term": float(prop),
            "trend": float(trend),
            "trend_term": float(trend_term),
            "sigma": float(sigma),
            "vol_term": float(vol_term),
            "mean_err": float(self.mean_err),
            "step_raw": float(raw),
            "step": float(step),
            "step_cap": float(cap),
            "limit": float(self.limit),
            "setpoint_ms": float(sp),
            "latency_ms": float(lat) if math.isfinite(lat) else -1.0,
            "arrival_rps": float(arrival) if math.isfinite(arrival) else -1.0,
            "admitted_rps": float(admitted) if math.isfinite(admitted) else -1.0,
            "setpoint_changed": int(sp_changed),
            "ticks_since_setpoint_change": int(self.n_since_change),
            "saturated": int(saturated),
            "regime": regime,
            "tick_count": int(self.n_ticks),
        }
        return {"limit": float(self.limit), "telemetry": telemetry}

    # ------------------------------------------------------------- persistence
    def snapshot(self) -> str:
        state = {k: getattr(self, k) for k in _STATE_KEYS}
        return json.dumps({"v": 1, "state": state}, sort_keys=True)

    @classmethod
    def restore(cls, blob: str) -> "Controller":
        data = json.loads(blob)
        st = data["state"]
        obj = cls({
            "min_limit": st["min_limit"],
            "max_limit": st["max_limit"],
            "initial_limit": st["initial_limit"],
        })
        for k in _STATE_KEYS:
            v = st[k]
            if k in ("n_since_change", "n_ticks", "n_setpoint_changes"):
                v = int(v)
            elif v is not None:
                v = float(v)
            setattr(obj, k, v)
        return obj
