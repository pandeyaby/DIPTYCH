"""Adaptive concurrency controller.

Regulates a concurrency limit so that observed latency tracks a setpoint.

Design summary
--------------
* The limit is the only integrated quantity and it is always clamped to
  ``[min_limit, max_limit]``, so there is no hidden integrator that can wind
  up while the controller sits at a bound.
* Each tick produces an additive correction ``delta = -gain * signal * scale``
  where ``scale = max_limit - min_limit`` and ``gain`` depends on the sign of
  the signal: cutting uses ``K_DOWN``, growing uses ``K_UP`` with
  ``K_DOWN / K_UP = 2.5``.  Because corrections are additive and independent of
  the current limit, they depend only on the observation history.
* ``signal = error + trend`` where ``error`` is the relative deviation from an
  effective setpoint and ``trend`` is an exponentially weighted average of the
  tick-to-tick change in relative latency (recent samples dominate).  The trend
  contribution is capped at ``|error|`` so a single response can never exceed
  twice the proportional response to the immediately observed error.
* The effective setpoint is the nominal setpoint reduced by a small penalty
  proportional to the recent standard deviation of relative latency, so noisier
  workloads converge to a lower limit.  Asymmetric gains also push the
  equilibrium lower on high-variance workloads.
* All history (previous sample, trend, mean, variance, saturation counter) is
  discarded when ``setpoint_ms`` changes; the limit itself is retained.
* Pure floating point arithmetic on a fixed evaluation order; no hashing,
  randomness, clocks or I/O.  Snapshots serialise floats as hex strings so the
  round trip is bit exact.
"""

import json
import math


def _clamp(value, lo, hi):
    if value < lo:
        return lo
    if value > hi:
        return hi
    return value


def _finite(value, fallback):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return fallback
    if math.isnan(value) or math.isinf(value):
        return fallback
    return value


class Controller:
    # Gains applied to the control signal (units: fraction of the limit range).
    K_DOWN = 0.05          # cut gain
    K_UP = 0.02            # grow gain  (K_DOWN / K_UP = 2.5 > 2)

    # Trend (recency) term.
    TREND_ALPHA = 0.5      # EWMA factor for tick-to-tick change of relative latency
    TREND_GAIN = 1.0       # weight of the trend term in the control signal

    # Volatility statistics.
    STAT_ALPHA = 0.1       # EWMA factor for mean / variance of relative latency
    VOL_GAIN = 0.1         # setpoint reduction per unit of relative std-dev
    VOL_CAP = 1.0          # cap on relative std-dev used for the penalty

    SIGNAL_CAP = 2.0       # symmetric cap on the control signal

    _FLOAT_FIELDS = (
        "min_limit", "max_limit", "initial_limit", "limit", "setpoint",
        "prev_r", "trend", "mean", "var",
    )
    _INT_FIELDS = (
        "have_setpoint", "have_prev", "n_since_reset", "sat_ticks", "ticks",
    )

    # ------------------------------------------------------------------ init
    def __init__(self, config):
        config = config or {}
        min_limit = _finite(config.get("min_limit", 1.0), 1.0)
        max_limit = _finite(config.get("max_limit", 200.0), 200.0)
        if max_limit < min_limit:
            min_limit, max_limit = max_limit, min_limit
        self.min_limit = min_limit
        self.max_limit = max_limit
        initial = _finite(config.get("initial_limit", 50.0), 50.0)
        self.initial_limit = _clamp(initial, self.min_limit, self.max_limit)

        self.limit = self.initial_limit
        self.setpoint = 0.0
        self.have_setpoint = 0
        self.ticks = 0
        self._reset_history()

    def _reset_history(self):
        self.prev_r = 0.0
        self.have_prev = 0
        self.trend = 0.0
        self.mean = 0.0
        self.var = 0.0
        self.n_since_reset = 0
        self.sat_ticks = 0

    # ------------------------------------------------------------------ tick
    def tick(self, obs):
        obs = obs or {}

        # --- parse observation -------------------------------------------
        prev_sp = self.setpoint if self.have_setpoint else 1.0
        sp = _finite(obs.get("setpoint_ms", prev_sp), prev_sp)
        if sp <= 0.0:
            sp = prev_sp if prev_sp > 0.0 else 1.0
        lat = _finite(obs.get("latency_ms", sp), sp)
        if lat < 0.0:
            lat = 0.0
        t_index = obs.get("t", self.ticks)
        try:
            t_index = int(t_index)
        except (TypeError, ValueError):
            t_index = self.ticks

        # --- setpoint change: forget all history --------------------------
        setpoint_changed = 0
        if self.have_setpoint and sp != self.setpoint:
            self._reset_history()
            setpoint_changed = 1
        self.setpoint = sp
        self.have_setpoint = 1

        # --- relative latency and recency-weighted trend ------------------
        r = (lat - sp) / sp
        if not self.have_prev:
            d = 0.0
            self.mean = r
            self.var = 0.0
            self.have_prev = 1
        else:
            d = r - self.prev_r
            self.mean = self.mean + self.STAT_ALPHA * (r - self.mean)
            dev = r - self.mean
            self.var = self.var + self.STAT_ALPHA * (dev * dev - self.var)
        self.prev_r = r
        self.trend = self.trend + self.TREND_ALPHA * (d - self.trend)
        self.n_since_reset += 1

        # --- volatility penalty on the setpoint ---------------------------
        var = self.var if self.var > 0.0 else 0.0
        sd = math.sqrt(var)
        penalty = self.VOL_GAIN * (sd if sd < self.VOL_CAP else self.VOL_CAP)
        sp_eff = sp * (1.0 - penalty)

        # --- control signal ----------------------------------------------
        error = (lat - sp_eff) / sp
        cap = error if error >= 0.0 else -error
        trend_term = _clamp(self.TREND_GAIN * self.trend, -cap, cap)
        signal = _clamp(error + trend_term, -self.SIGNAL_CAP, self.SIGNAL_CAP)

        scale = self.max_limit - self.min_limit
        if signal > 0.0:
            delta = -self.K_DOWN * signal * scale
            direction = "down"
        elif signal < 0.0:
            delta = -self.K_UP * signal * scale
            direction = "up"
        else:
            delta = 0.0
            direction = "hold"

        # --- apply, clamp (this is the anti-windup) -----------------------
        old_limit = self.limit
        new_limit = _clamp(old_limit + delta, self.min_limit, self.max_limit)
        applied = new_limit - old_limit
        self.limit = new_limit

        saturated = 1 if new_limit >= self.max_limit else 0
        if saturated:
            self.sat_ticks += 1
        else:
            self.sat_ticks = 0
        self.ticks += 1

        telemetry = {
            "t": t_index,
            "limit": float(new_limit),
            "delta_requested": float(delta),
            "delta_applied": float(applied),
            "direction": direction,
            "latency_ms": float(lat),
            "setpoint_ms": float(sp),
            "effective_setpoint_ms": float(sp_eff),
            "relative_latency": float(r),
            "error": float(error),
            "trend": float(self.trend),
            "trend_term": float(trend_term),
            "signal": float(signal),
            "latency_sd": float(sd),
            "volatility_penalty": float(penalty),
            "saturated": saturated,
            "saturated_ticks": int(self.sat_ticks),
            "setpoint_changed": setpoint_changed,
            "ticks_since_reset": int(self.n_since_reset),
            "ticks_total": int(self.ticks),
        }
        return {"limit": float(new_limit), "telemetry": telemetry}

    # ------------------------------------------------------------ snapshots
    def snapshot(self):
        state = {}
        for name in self._FLOAT_FIELDS:
            state[name] = float(getattr(self, name)).hex()
        for name in self._INT_FIELDS:
            state[name] = int(getattr(self, name))
        return json.dumps({"version": 1, "state": state}, sort_keys=True)

    @classmethod
    def restore(cls, blob):
        payload = json.loads(blob)
        state = payload["state"]
        ctl = cls.__new__(cls)
        for name in cls._FLOAT_FIELDS:
            setattr(ctl, name, float.fromhex(state[name]))
        for name in cls._INT_FIELDS:
            setattr(ctl, name, int(state[name]))
        return ctl
