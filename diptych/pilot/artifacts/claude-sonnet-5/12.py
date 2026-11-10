"""Adaptive concurrency controller.

Regulates a concurrency `limit` so that observed latency tracks a
latency setpoint, using an exponentially-weighted error estimate with
an asymmetric (fast-down / slow-up) response, variance-aware damping
of the growth rate, and anti-windup handling for sustained saturation.
"""

import json
import math


class Controller:
    _ALPHA_ERROR = 0.35   # EWMA decay for the error estimate (recency weighting)
    _ALPHA_VAR = 0.2       # EWMA decay for the error-volatility estimate
    _K_DOWN = 0.9          # fractional decrease gain (per unit error)
    _K_UP = 0.3            # fractional increase gain (per unit error)
    _KAPPA_VAR = 1.5       # how strongly volatility damps the increase gain

    def __init__(self, config):
        self.min_limit = float(config["min_limit"])
        self.max_limit = float(config["max_limit"])
        initial_limit = float(config.get("initial_limit", self.min_limit))
        self.limit = min(max(initial_limit, self.min_limit), self.max_limit)

        self.ewma_error = 0.0
        self.ewma_var = 0.0
        self.sat_dir = 0          # -1 saturated low, 0 none, +1 saturated high
        self.setpoint_prev = None
        self.initialized = False
        self.t_last = -1

    def _reset_filters(self, error):
        self.ewma_error = error
        self.ewma_var = 0.0
        self.sat_dir = 0

    def tick(self, obs):
        t = obs["t"]
        latency_ms = float(obs["latency_ms"])
        setpoint_ms = float(obs["setpoint_ms"])
        arrival_rps = float(obs["arrival_rps"])
        admitted_rps = float(obs["admitted_rps"])

        error = (latency_ms - setpoint_ms) / setpoint_ms if setpoint_ms else 0.0

        reset_event = 0

        if not self.initialized:
            self._reset_filters(error)
            self.setpoint_prev = setpoint_ms
            self.initialized = True
            reset_event = 1
        elif setpoint_ms != self.setpoint_prev:
            # Setpoint changed: history under the old target is no longer
            # meaningful, so corrections must start fresh from this error.
            self._reset_filters(error)
            self.setpoint_prev = setpoint_ms
            reset_event = 1
        elif self.sat_dir == 1 and error > 0.0:
            # Was pinned at max_limit (consistently fast); now an excursion
            # above setpoint appears. Respond to this error alone, not to
            # how long saturation lasted (anti-windup).
            self._reset_filters(error)
            reset_event = 1
        elif self.sat_dir == -1 and error < 0.0:
            # Symmetric case: was pinned at min_limit, now an excursion
            # below setpoint appears.
            self._reset_filters(error)
            reset_event = 1
        else:
            prev_ewma = self.ewma_error
            self.ewma_error = self._ALPHA_ERROR * error + (1.0 - self._ALPHA_ERROR) * prev_ewma
            dev = error - prev_ewma
            self.ewma_var = self._ALPHA_VAR * (dev * dev) + (1.0 - self._ALPHA_VAR) * self.ewma_var

        std_dev = math.sqrt(self.ewma_var)
        k_up_eff = self._K_UP / (1.0 + self._KAPPA_VAR * std_dev)

        if self.ewma_error > 0.0:
            raw_limit = self.limit * (1.0 - self._K_DOWN * self.ewma_error)
            direction = "down"
        elif self.ewma_error < 0.0:
            raw_limit = self.limit * (1.0 - k_up_eff * self.ewma_error)
            direction = "up"
        else:
            raw_limit = self.limit
            direction = "hold"

        new_limit = min(max(raw_limit, self.min_limit), self.max_limit)

        if raw_limit > self.max_limit:
            self.sat_dir = 1
        elif raw_limit < self.min_limit:
            self.sat_dir = -1
        else:
            self.sat_dir = 0

        self.limit = new_limit
        self.t_last = t

        telemetry = {
            "t": int(t),
            "error": float(error),
            "ewma_error": float(self.ewma_error),
            "ewma_var": float(self.ewma_var),
            "std_dev": float(std_dev),
            "k_up_eff": float(k_up_eff),
            "raw_limit": float(raw_limit),
            "limit": float(self.limit),
            "saturated": int(self.sat_dir),
            "reset_event": int(reset_event),
            "setpoint_ms": float(setpoint_ms),
            "direction": direction,
            "arrival_rps": float(arrival_rps),
            "admitted_rps": float(admitted_rps),
        }

        return {"limit": self.limit, "telemetry": telemetry}

    def snapshot(self):
        state = {
            "min_limit": self.min_limit,
            "max_limit": self.max_limit,
            "limit": self.limit,
            "ewma_error": self.ewma_error,
            "ewma_var": self.ewma_var,
            "sat_dir": self.sat_dir,
            "setpoint_prev": self.setpoint_prev,
            "initialized": self.initialized,
            "t_last": self.t_last,
        }
        return json.dumps(state, sort_keys=True)

    @classmethod
    def restore(cls, blob):
        state = json.loads(blob)
        obj = cls.__new__(cls)
        obj.min_limit = state["min_limit"]
        obj.max_limit = state["max_limit"]
        obj.limit = state["limit"]
        obj.ewma_error = state["ewma_error"]
        obj.ewma_var = state["ewma_var"]
        obj.sat_dir = state["sat_dir"]
        obj.setpoint_prev = state["setpoint_prev"]
        obj.initialized = state["initialized"]
        obj.t_last = state["t_last"]
        return obj
