"""Adaptive concurrency controller.

Regulates a concurrency limit so that observed latency tracks a setpoint.
The controller is integrator-free (so it cannot wind up), uses multiplicative
corrections (so a correction depends only on observations, never on the
absolute limit it started from), and combines three signals:

* the normalised error of the current sample (proportional term),
* a trend term (fast EWMA minus slow EWMA of the error) so recent movement
  dominates older history,
* a volatility term (EWMA standard deviation of the error) that shifts the
  effective error upward on noisy workloads, driving a more conservative limit.

Downward gain is three times the upward gain and the per-tick step caps are
asymmetric as well, so a positive deviation is always corrected more than
twice as hard as an equal negative one. All history is discarded when the
setpoint changes. Pure standard library, no I/O, no randomness, no clocks.
"""

from __future__ import annotations

import json
import math


class Controller:
    # Proportional gains (downward is 3x upward).
    _KP_DOWN = 0.6
    _KP_UP = 0.2
    # Weight of the trend (fast EWMA - slow EWMA) inside the control signal.
    _KT = 0.4
    # Weight of the volatility (EWMA std of error) inside the control signal.
    _KV = 0.5
    # EWMA smoothing factors.
    _A_FAST = 0.5
    _A_SLOW = 0.15
    _A_VAR = 0.2
    # Per-tick relative step caps (asymmetric, down cap >= 2x up cap).
    _MAX_DOWN = 0.6
    _MAX_UP = 0.15
    _EPS = 1e-9
    _VERSION = 1

    # ------------------------------------------------------------------ init
    def __init__(self, config: dict):
        config = dict(config or {})
        self._min_limit = float(config.get("min_limit", 1.0))
        self._max_limit = float(config.get("max_limit", 200.0))
        if self._max_limit < self._min_limit:
            self._max_limit = self._min_limit
        initial = float(config.get("initial_limit", 50.0))
        self._limit = self._clamp(initial)

        self._t = 0                 # internal tick counter (fallback for obs["t"])
        self._setpoint = None       # last seen setpoint (None before first tick)
        self._n = 0                 # observations since last history reset
        self._mean_fast = 0.0
        self._mean_slow = 0.0
        self._var = 0.0

    # --------------------------------------------------------------- helpers
    def _clamp(self, value: float) -> float:
        if value < self._min_limit:
            return self._min_limit
        if value > self._max_limit:
            return self._max_limit
        return value

    def _reset_history(self) -> None:
        self._n = 0
        self._mean_fast = 0.0
        self._mean_slow = 0.0
        self._var = 0.0

    # ------------------------------------------------------------------ tick
    def tick(self, obs: dict) -> dict:
        t = int(obs.get("t", self._t))
        latency = float(obs.get("latency_ms", 0.0))
        default_sp = self._setpoint if self._setpoint is not None else 1.0
        setpoint = float(obs.get("setpoint_ms", default_sp))
        arrival = float(obs.get("arrival_rps", 0.0))
        admitted = float(obs.get("admitted_rps", 0.0))

        # Setpoint-change quiet: discard everything learned before the change.
        reset = 0
        if self._setpoint is None:
            self._setpoint = setpoint
            self._reset_history()
        elif setpoint != self._setpoint:
            self._setpoint = setpoint
            self._reset_history()
            reset = 1

        sp = setpoint if setpoint > self._EPS else self._EPS
        error = (latency - sp) / sp

        # Update recency-weighted statistics.
        if self._n == 0:
            self._mean_fast = error
            self._mean_slow = error
            self._var = 0.0
        else:
            dev = error - self._mean_slow
            self._var += self._A_VAR * (dev * dev - self._var)
            self._mean_fast += self._A_FAST * (error - self._mean_fast)
            self._mean_slow += self._A_SLOW * (error - self._mean_slow)
        self._n += 1

        trend = self._mean_fast - self._mean_slow
        std = math.sqrt(self._var) if self._var > 0.0 else 0.0

        # Effective control signal: positive means "too slow / too risky".
        signal = error + self._KT * trend + self._KV * std

        if signal > 0.0:
            u = -min(self._KP_DOWN * signal, self._MAX_DOWN)
        else:
            u = min(self._KP_UP * (-signal), self._MAX_UP)

        prev_limit = self._limit
        new_limit = self._clamp(prev_limit * (1.0 + u))
        self._limit = new_limit
        self._t = t + 1

        saturated_high = 1 if new_limit >= self._max_limit - self._EPS else 0
        saturated_low = 1 if new_limit <= self._min_limit + self._EPS else 0
        utilization = admitted / arrival if arrival > 0.0 else 0.0

        telemetry = {
            "t": t,
            "setpoint_ms": float(setpoint),
            "latency_ms": float(latency),
            "error": float(error),
            "mean_fast": float(self._mean_fast),
            "mean_slow": float(self._mean_slow),
            "trend": float(trend),
            "std": float(std),
            "signal": float(signal),
            "u": float(u),
            "prev_limit": float(prev_limit),
            "limit": float(new_limit),
            "saturated_high": saturated_high,
            "saturated_low": saturated_low,
            "setpoint_reset": reset,
            "history_n": int(self._n),
            "utilization": float(utilization),
            "mode": "cut" if u < 0.0 else ("grow" if u > 0.0 else "hold"),
        }
        return {"limit": float(new_limit), "telemetry": telemetry}

    # -------------------------------------------------------------- snapshot
    def snapshot(self) -> str:
        state = {
            "version": self._VERSION,
            "min_limit": self._min_limit,
            "max_limit": self._max_limit,
            "limit": self._limit,
            "t": self._t,
            "setpoint": self._setpoint,
            "n": self._n,
            "mean_fast": self._mean_fast,
            "mean_slow": self._mean_slow,
            "var": self._var,
        }
        return json.dumps(state, sort_keys=True, separators=(",", ":"))

    @classmethod
    def restore(cls, blob: str) -> "Controller":
        state = json.loads(blob)
        obj = cls.__new__(cls)
        obj._min_limit = float(state["min_limit"])
        obj._max_limit = float(state["max_limit"])
        obj._limit = float(state["limit"])
        obj._t = int(state["t"])
        sp = state.get("setpoint")
        obj._setpoint = None if sp is None else float(sp)
        obj._n = int(state["n"])
        obj._mean_fast = float(state["mean_fast"])
        obj._mean_slow = float(state["mean_slow"])
        obj._var = float(state["var"])
        return obj
