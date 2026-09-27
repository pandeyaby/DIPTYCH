"""Adaptive concurrency controller.

Regulates a concurrency limit so that observed latency tracks a latency
setpoint.  Pure Python standard library, deterministic, no I/O.

Control structure (per tick):

    err      = (latency - setpoint) / setpoint            relative error
    err_f    = EWMA(err)                                  recency-weighted level
    trend    = EWMA(err_t - err_{t-1})                    recency-weighted slope
    vol      = sqrt(EWMA(residual^2))                     latency volatility
    margin   = min(c * vol, cap)                          safety margin
    signal   = err_f + k_trend * trend + margin
    step     = -gain(sign(signal)) * signal * span        additive correction
    limit    = clamp(limit + step)

Design notes mapped to the requirements:

* Asymmetric response: the gain applied to a positive signal (latency above
  target -> shrink) is three times the gain applied to a negative signal.
* Trend recency: the level term is an EWMA and a smoothed derivative term is
  added, so recent movement dominates the correction.
* Anti-windup: the limit itself is the only integrator and it is clamped; all
  filters are bounded EWMAs, and when the controller leaves a saturated bound
  the filters are re-initialised from the current observation, so the first
  response is exactly a function of the immediately observed error.
* Volatility suppression: the effective target is lowered by a margin that
  grows with the observed residual variance, and the asymmetric gain itself
  biases a noisy loop toward a lower equilibrium.
* Setpoint-change quiet: every filter is reset when the setpoint changes and
  the first sample after a reset initialises the filters directly, so
  corrections after the change are a function of post-change observations
  only (the limit level carries over, the correction does not).
* Determinism / round-trip: state is a handful of floats and ints serialised
  with json (repr round-trips floats exactly); no hashing of unordered
  containers, no randomness, no clock.
* Telemetry schema: a fixed tuple of keys is emitted every tick.
"""

import json
import math


class Controller:
    # --- tuning constants (class-level so they are part of the code, not state)
    _ALPHA_FAST = 0.5        # EWMA weight for the error level
    _ALPHA_TREND = 0.5       # EWMA weight for the error derivative
    _ALPHA_VAR = 0.1         # EWMA weight for residual variance
    _K_TREND = 2.0           # weight of the trend term in the signal
    _GAIN_DOWN = 0.05        # gain when the signal says "shrink"
    _GAIN_UP = 0.05 / 3.0    # gain when the signal says "grow" (1/3 of down)
    _VOL_COEF = 0.5          # margin = VOL_COEF * volatility
    _VOL_MAX_MARGIN = 0.3    # cap on the volatility margin (relative units)
    _SIGNAL_MIN = -1.0
    _SIGNAL_MAX = 3.0
    _SNAPSHOT_VERSION = 1

    _TELEMETRY_KEYS = (
        "t",
        "limit",
        "prev_limit",
        "step",
        "latency_ms",
        "setpoint_ms",
        "target_ms",
        "err",
        "err_filtered",
        "trend",
        "volatility",
        "margin",
        "signal",
        "gain",
        "mode",
        "reset",
        "n_since_reset",
        "at_max",
        "at_min",
        "arrival_rps",
        "admitted_rps",
        "utilization",
    )

    # ------------------------------------------------------------------ init
    def __init__(self, config):
        config = dict(config or {})
        min_limit = float(config.get("min_limit", 1.0))
        max_limit = float(config.get("max_limit", 200.0))
        if max_limit < min_limit:
            max_limit = min_limit
        initial_limit = float(config.get("initial_limit", 50.0))

        self.min_limit = min_limit
        self.max_limit = max_limit
        self.initial_limit = self._clamp(initial_limit)
        self.span = self.max_limit - self.min_limit

        self.limit = self.initial_limit
        self.setpoint = None          # last seen setpoint (None until first tick)
        self.ticks = 0                # number of ticks processed

        # filter state
        self.err_f = 0.0
        self.trend = 0.0
        self.prev_err = 0.0
        self.var = 0.0
        self.n = 0                    # samples since last filter reset

    # --------------------------------------------------------------- helpers
    def _clamp(self, value):
        if value < self.min_limit:
            return self.min_limit
        if value > self.max_limit:
            return self.max_limit
        return value

    def _reset_filters(self):
        self.err_f = 0.0
        self.trend = 0.0
        self.prev_err = 0.0
        self.var = 0.0
        self.n = 0

    @staticmethod
    def _f(obs, key, default):
        value = obs.get(key, default)
        try:
            value = float(value)
        except (TypeError, ValueError):
            value = float(default)
        if value != value or value in (float("inf"), float("-inf")):
            value = float(default)
        return value

    # ------------------------------------------------------------------ tick
    def tick(self, obs):
        obs = obs or {}
        try:
            t = int(obs.get("t", self.ticks))
        except (TypeError, ValueError):
            t = self.ticks

        fallback_sp = self.setpoint if self.setpoint is not None else 0.0
        latency = self._f(obs, "latency_ms", 0.0)
        setpoint = self._f(obs, "setpoint_ms", fallback_sp)
        arrival = self._f(obs, "arrival_rps", 0.0)
        admitted = self._f(obs, "admitted_rps", 0.0)

        # --- setpoint bookkeeping / quiet period on change
        reset = "none"
        if self.setpoint is None:
            reset = "init"
            self._reset_filters()
        elif setpoint != self.setpoint:
            reset = "setpoint"
            self._reset_filters()
        self.setpoint = setpoint

        sp_safe = abs(setpoint)
        if sp_safe < 1e-9:
            sp_safe = 1e-9
        err = (latency - setpoint) / sp_safe

        # --- anti-windup: leaving a saturated bound starts from the raw error
        prev_limit = self.limit
        at_max = 1 if prev_limit >= self.max_limit else 0
        at_min = 1 if prev_limit <= self.min_limit else 0
        if self.n > 0 and ((at_max and err > 0.0) or (at_min and err < 0.0)):
            self._reset_filters()
            reset = "saturation_exit"

        # --- filters
        if self.n == 0:
            self.err_f = err
            self.trend = 0.0
            self.var = 0.0
        else:
            residual = err - self.err_f
            self.var = (1.0 - self._ALPHA_VAR) * self.var \
                + self._ALPHA_VAR * residual * residual
            self.err_f = (1.0 - self._ALPHA_FAST) * self.err_f \
                + self._ALPHA_FAST * err
            self.trend = (1.0 - self._ALPHA_TREND) * self.trend \
                + self._ALPHA_TREND * (err - self.prev_err)
        self.prev_err = err
        self.n += 1

        volatility = math.sqrt(self.var) if self.var > 0.0 else 0.0
        margin = self._VOL_COEF * volatility
        if margin > self._VOL_MAX_MARGIN:
            margin = self._VOL_MAX_MARGIN

        # --- control signal and asymmetric correction
        signal = self.err_f + self._K_TREND * self.trend + margin
        if signal < self._SIGNAL_MIN:
            signal = self._SIGNAL_MIN
        elif signal > self._SIGNAL_MAX:
            signal = self._SIGNAL_MAX

        if signal > 0.0:
            gain = self._GAIN_DOWN
            mode = "down"
        elif signal < 0.0:
            gain = self._GAIN_UP
            mode = "up"
        else:
            gain = 0.0
            mode = "hold"

        step = -gain * signal * self.span
        new_limit = self._clamp(prev_limit + step)
        self.limit = new_limit
        self.ticks += 1

        utilization = admitted / arrival if arrival > 0.0 else 0.0
        target_ms = setpoint * (1.0 - margin)

        telemetry = {
            "t": t,
            "limit": new_limit,
            "prev_limit": prev_limit,
            "step": new_limit - prev_limit,
            "latency_ms": latency,
            "setpoint_ms": setpoint,
            "target_ms": target_ms,
            "err": err,
            "err_filtered": self.err_f,
            "trend": self.trend,
            "volatility": volatility,
            "margin": margin,
            "signal": signal,
            "gain": gain,
            "mode": mode,
            "reset": reset,
            "n_since_reset": self.n,
            "at_max": at_max,
            "at_min": at_min,
            "arrival_rps": arrival,
            "admitted_rps": admitted,
            "utilization": utilization,
        }
        # Guarantee a stable schema regardless of code paths above.
        telemetry = {k: telemetry[k] for k in self._TELEMETRY_KEYS}

        return {"limit": new_limit, "telemetry": telemetry}

    # ------------------------------------------------------------- snapshot
    def snapshot(self):
        state = {
            "version": self._SNAPSHOT_VERSION,
            "config": {
                "min_limit": self.min_limit,
                "max_limit": self.max_limit,
                "initial_limit": self.initial_limit,
            },
            "state": {
                "limit": self.limit,
                "setpoint": self.setpoint,
                "ticks": self.ticks,
                "err_f": self.err_f,
                "trend": self.trend,
                "prev_err": self.prev_err,
                "var": self.var,
                "n": self.n,
            },
        }
        return json.dumps(state, sort_keys=True, separators=(",", ":"))

    @classmethod
    def restore(cls, blob):
        data = json.loads(blob)
        cfg = data.get("config", {})
        st = data.get("state", {})

        obj = cls(
            {
                "min_limit": cfg.get("min_limit", 1.0),
                "max_limit": cfg.get("max_limit", 200.0),
                "initial_limit": cfg.get("initial_limit", 50.0),
            }
        )
        obj.limit = float(st.get("limit", obj.initial_limit))
        sp = st.get("setpoint", None)
        obj.setpoint = None if sp is None else float(sp)
        obj.ticks = int(st.get("ticks", 0))
        obj.err_f = float(st.get("err_f", 0.0))
        obj.trend = float(st.get("trend", 0.0))
        obj.prev_err = float(st.get("prev_err", 0.0))
        obj.var = float(st.get("var", 0.0))
        obj.n = int(st.get("n", 0))
        return obj
