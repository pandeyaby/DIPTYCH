"""Adaptive concurrency controller.

Regulates a service's concurrency limit so that observed latency tracks a
latency setpoint.  Pure standard library, deterministic, snapshot/restore-able.

Design summary
--------------
* The limit itself is the integrating state (no separate integrator), so it
  can never wind up past the clamp bounds.
* The normalized error ``e = (latency - setpoint) / setpoint`` feeds two EMAs
  (fast and slow).  The fast EMA is the level term; ``fast - slow`` is a trend
  term that rewards improving latency and penalises worsening latency, which
  gives recent observations more weight than old ones.
* An EMA variance estimate of ``e`` adds a volatility margin to the control
  signal and also imposes a soft ceiling on the limit, so noisier workloads
  converge to a more conservative limit.
* Corrections are asymmetric: the downward gain is three times the upward gain
  (and the per-tick step caps preserve that ratio).
* All filter state is reset when the setpoint changes, so post-change
  corrections depend only on post-change observations.
* Corrections are additive and scaled by the configured limit span, so they do
  not depend on the current limit (except through clamping).
"""

import json
import math


class Controller:
    # Filter parameters
    ALPHA_FAST = 0.5
    ALPHA_SLOW = 0.15
    ALPHA_VAR = 0.1
    # Signal composition
    K_TREND = 2.0
    K_VOL = 0.5
    K_CAP = 1.0
    # Gains (downward strictly exceeds 2x upward)
    GAIN_UP = 0.06
    GAIN_DOWN = 0.18
    STEP_UP_MAX = 0.15
    STEP_DOWN_MAX = 0.5
    # Normalized error clamp
    ERR_LO = -1.0
    ERR_HI = 5.0

    _STATE_FLOATS = (
        "min_limit",
        "max_limit",
        "initial_limit",
        "limit",
        "span",
        "err_fast",
        "err_slow",
        "mean",
        "var",
        "last_setpoint",
        "last_delta",
        "last_signal",
    )
    _STATE_INTS = ("n", "last_t")

    # ------------------------------------------------------------------ init
    def __init__(self, config):
        config = config or {}
        self.min_limit = self._finite(config.get("min_limit", 1.0), 1.0)
        self.max_limit = self._finite(config.get("max_limit", 200.0), 200.0)
        if self.max_limit < self.min_limit:
            self.max_limit = self.min_limit
        self.initial_limit = self._finite(config.get("initial_limit", 50.0), 50.0)
        self.limit = self._clamp(self.initial_limit)
        self.span = self.max_limit - self.min_limit
        if self.span < 0.0:
            self.span = 0.0

        # Filter state (reset on setpoint change)
        self.n = 0
        self.err_fast = 0.0
        self.err_slow = 0.0
        self.mean = 0.0
        self.var = 0.0
        self.last_setpoint = float("nan")  # NaN != anything -> forces reset
        self.has_setpoint = False

        # Bookkeeping
        self.last_t = -1
        self.last_delta = 0.0
        self.last_signal = 0.0

    # --------------------------------------------------------------- helpers
    @staticmethod
    def _finite(value, default):
        try:
            v = float(value)
        except (TypeError, ValueError):
            return float(default)
        if not math.isfinite(v):
            return float(default)
        return v

    def _clamp(self, value):
        if value < self.min_limit:
            return self.min_limit
        if value > self.max_limit:
            return self.max_limit
        return value

    def _reset_filters(self):
        self.n = 0
        self.err_fast = 0.0
        self.err_slow = 0.0
        self.mean = 0.0
        self.var = 0.0

    # ------------------------------------------------------------------ tick
    def tick(self, obs):
        obs = obs or {}
        t = obs.get("t", self.last_t + 1)
        try:
            t = int(t)
        except (TypeError, ValueError):
            t = self.last_t + 1

        latency = self._finite(obs.get("latency_ms", 0.0), 0.0)
        setpoint_raw = obs.get("setpoint_ms", 0.0)
        setpoint = self._finite(setpoint_raw, 1.0)
        arrival = self._finite(obs.get("arrival_rps", 0.0), 0.0)
        admitted = self._finite(obs.get("admitted_rps", 0.0), 0.0)

        # --- setpoint-change quiet: wipe all history-dependent filter state
        reset = 0
        if (not self.has_setpoint) or (setpoint != self.last_setpoint):
            self._reset_filters()
            reset = 1
        self.last_setpoint = setpoint
        self.has_setpoint = True

        # --- normalized error
        sp = setpoint if setpoint > 0.0 else 1.0
        e = (latency - sp) / sp
        if not math.isfinite(e):
            e = 0.0
        if e < self.ERR_LO:
            e = self.ERR_LO
        elif e > self.ERR_HI:
            e = self.ERR_HI

        # --- filters (first sample after reset seeds the filters exactly)
        if self.n == 0:
            self.err_fast = e
            self.err_slow = e
            self.mean = e
            self.var = 0.0
        else:
            self.err_fast += self.ALPHA_FAST * (e - self.err_fast)
            self.err_slow += self.ALPHA_SLOW * (e - self.err_slow)
            diff = e - self.mean
            self.mean += self.ALPHA_VAR * diff
            self.var = (1.0 - self.ALPHA_VAR) * (self.var + self.ALPHA_VAR * diff * diff)
        self.n += 1

        trend = self.err_fast - self.err_slow
        vol = math.sqrt(self.var) if self.var > 0.0 else 0.0
        signal = self.err_fast + self.K_TREND * trend + self.K_VOL * vol

        # --- asymmetric, bounded correction (additive, scaled by span)
        if signal > 0.0:
            step = self.GAIN_DOWN * signal
            if step > self.STEP_DOWN_MAX:
                step = self.STEP_DOWN_MAX
            delta = -step * self.span
            mode = "down"
        elif signal < 0.0:
            step = self.GAIN_UP * (-signal)
            if step > self.STEP_UP_MAX:
                step = self.STEP_UP_MAX
            delta = step * self.span
            mode = "up"
        else:
            delta = 0.0
            mode = "hold"

        # --- volatility ceiling
        cap = self.max_limit / (1.0 + self.K_CAP * self.var)
        if cap < self.min_limit:
            cap = self.min_limit

        new_limit = self.limit + delta
        if new_limit > cap:
            new_limit = cap
        new_limit = self._clamp(new_limit)

        actual_delta = new_limit - self.limit
        self.limit = new_limit
        self.last_t = t
        self.last_delta = actual_delta
        self.last_signal = signal

        if self.limit >= self.max_limit:
            saturated = 1
        elif self.limit <= self.min_limit:
            saturated = -1
        else:
            saturated = 0

        utilization = admitted / arrival if arrival > 0.0 else 0.0

        telemetry = {
            "t": int(t),
            "limit": float(self.limit),
            "delta": float(actual_delta),
            "signal": float(signal),
            "error": float(e),
            "err_fast": float(self.err_fast),
            "err_slow": float(self.err_slow),
            "trend": float(trend),
            "volatility": float(vol),
            "cap": float(cap),
            "samples": int(self.n),
            "setpoint": float(setpoint),
            "saturated": int(saturated),
            "mode": mode,
            "reset": int(reset),
            "utilization": float(utilization),
            "offered_rps": float(arrival),
            "admitted_rps": float(admitted),
        }
        return {"limit": float(self.limit), "telemetry": telemetry}

    # ------------------------------------------------------------- snapshot
    def snapshot(self):
        state = {}
        for key in self._STATE_FLOATS:
            state[key] = float(getattr(self, key)).hex()
        for key in self._STATE_INTS:
            state[key] = int(getattr(self, key))
        state["has_setpoint"] = 1 if self.has_setpoint else 0
        return json.dumps({"version": 1, "state": state}, sort_keys=True, separators=(",", ":"))

    @classmethod
    def restore(cls, blob):
        data = json.loads(blob)
        state = data["state"]
        obj = cls.__new__(cls)
        for key in cls._STATE_FLOATS:
            setattr(obj, key, float.fromhex(state[key]))
        for key in cls._STATE_INTS:
            setattr(obj, key, int(state[key]))
        obj.has_setpoint = bool(int(state.get("has_setpoint", 0)))
        return obj
