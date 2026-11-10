import json
import math


class Controller:
    _VERSION = 1
    _ERROR_ALPHA = 0.55
    _TREND_ALPHA = 0.50
    _DOWN_GAIN = 0.18
    _UP_GAIN = 0.07
    _TREND_GAIN = 0.06
    _VOLATILITY_WEIGHT = 0.35
    _MAX_NORMALIZED_ERROR = 4.0

    def __init__(self, config: dict):
        if not isinstance(config, dict):
            raise TypeError("config must be a dict")

        self.min_limit = self._finite_float(
            config.get("min_limit", 1.0), "min_limit"
        )
        self.max_limit = self._finite_float(
            config.get("max_limit", 200.0), "max_limit"
        )
        self.initial_limit = self._finite_float(
            config.get("initial_limit", 50.0), "initial_limit"
        )

        if self.min_limit <= 0.0:
            raise ValueError("min_limit must be positive")
        if self.max_limit < self.min_limit:
            raise ValueError("max_limit must be at least min_limit")
        if not self.min_limit <= self.initial_limit <= self.max_limit:
            raise ValueError("initial_limit must be within configured bounds")

        self.limit = self.initial_limit
        self._setpoint = None
        self._initialized = False
        self._filtered_error = 0.0
        self._variance = 0.0
        self._trend = 0.0
        self._previous_error = 0.0
        self._blocked_high = False
        self._blocked_low = False
        self._last_t = -1

    def tick(self, obs: dict) -> dict:
        if not isinstance(obs, dict):
            raise TypeError("obs must be a dict")

        t = obs.get("t")
        if isinstance(t, bool) or not isinstance(t, int):
            raise TypeError("t must be an int")

        latency = self._finite_float(obs.get("latency_ms"), "latency_ms")
        setpoint = self._finite_float(obs.get("setpoint_ms"), "setpoint_ms")
        arrival = self._finite_float(obs.get("arrival_rps"), "arrival_rps")
        admitted = self._finite_float(obs.get("admitted_rps"), "admitted_rps")

        if latency < 0.0:
            raise ValueError("latency_ms must be nonnegative")
        if setpoint <= 0.0:
            raise ValueError("setpoint_ms must be positive")
        if arrival < 0.0 or admitted < 0.0:
            raise ValueError("traffic rates must be nonnegative")

        setpoint_reset = self._setpoint is None or setpoint != self._setpoint
        if setpoint_reset:
            self._reset_signals()
            self._setpoint = setpoint

        raw_error = (latency - setpoint) / setpoint
        raw_error = max(
            -self._MAX_NORMALIZED_ERROR,
            min(self._MAX_NORMALIZED_ERROR, raw_error),
        )

        # Discard outward-driving history when returning from a bound. This
        # makes the first inward response depend on the current excursion.
        if (
            (self._blocked_high and raw_error > 0.0)
            or (self._blocked_low and raw_error < 0.0)
        ):
            self._reset_signals()

        if not self._initialized:
            self._filtered_error = raw_error
            self._variance = 0.0
            self._trend = 0.0
            self._previous_error = raw_error
            self._initialized = True
        else:
            old_mean = self._filtered_error
            difference = raw_error - old_mean

            self._filtered_error = (
                old_mean + self._ERROR_ALPHA * difference
            )
            self._variance = (1.0 - self._ERROR_ALPHA) * (
                self._variance
                + self._ERROR_ALPHA * difference * difference
            )

            instantaneous_trend = raw_error - self._previous_error
            self._trend = (
                (1.0 - self._TREND_ALPHA) * self._trend
                + self._TREND_ALPHA * instantaneous_trend
            )
            self._previous_error = raw_error

        volatility = math.sqrt(max(0.0, self._variance))
        effective_error = (
            self._filtered_error
            + self._VOLATILITY_WEIGHT * volatility
        )

        if effective_error > 0.0:
            gain = self._DOWN_GAIN
            proportional = -gain * effective_error
        elif effective_error < 0.0:
            gain = self._UP_GAIN
            proportional = -gain * effective_error
        else:
            gain = 0.0
            proportional = 0.0

        span = self.max_limit - self.min_limit
        requested_delta = span * (
            proportional - self._TREND_GAIN * self._trend
        )

        old_limit = self.limit
        requested_limit = old_limit + requested_delta
        self.limit = max(
            self.min_limit,
            min(self.max_limit, requested_limit),
        )
        applied_delta = self.limit - old_limit

        self._blocked_high = (
            self.limit >= self.max_limit and requested_delta > 0.0
        )
        self._blocked_low = (
            self.limit <= self.min_limit and requested_delta < 0.0
        )
        self._last_t = t

        if self._blocked_high:
            saturation = "high"
        elif self._blocked_low:
            saturation = "low"
        else:
            saturation = "none"

        utilization = admitted / arrival if arrival > 0.0 else 0.0

        telemetry = {
            "algorithm": "adaptive_latency_v1",
            "t": t,
            "limit": self.limit,
            "raw_error": raw_error,
            "filtered_error": self._filtered_error,
            "trend": self._trend,
            "variance": self._variance,
            "volatility": volatility,
            "effective_error": effective_error,
            "gain": gain,
            "requested_delta": requested_delta,
            "applied_delta": applied_delta,
            "setpoint_ms": setpoint,
            "utilization": utilization,
            "setpoint_reset": int(setpoint_reset),
            "saturation": saturation,
        }
        return {"limit": self.limit, "telemetry": telemetry}

    def snapshot(self) -> str:
        data = {
            "version": self._VERSION,
            "config": {
                "min_limit": self.min_limit,
                "max_limit": self.max_limit,
                "initial_limit": self.initial_limit,
            },
            "state": {
                "limit": self.limit,
                "setpoint": self._setpoint,
                "initialized": self._initialized,
                "filtered_error": self._filtered_error,
                "variance": self._variance,
                "trend": self._trend,
                "previous_error": self._previous_error,
                "blocked_high": self._blocked_high,
                "blocked_low": self._blocked_low,
                "last_t": self._last_t,
            },
        }
        return json.dumps(
            data,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )

    @classmethod
    def restore(cls, blob: str) -> "Controller":
        if not isinstance(blob, str):
            raise TypeError("blob must be a string")

        data = json.loads(blob)
        if not isinstance(data, dict) or data.get("version") != cls._VERSION:
            raise ValueError("unsupported or invalid snapshot")

        config = data.get("config")
        state = data.get("state")
        if not isinstance(config, dict) or not isinstance(state, dict):
            raise ValueError("invalid snapshot")

        controller = cls(config)
        controller.limit = cls._finite_float(state["limit"], "limit")

        setpoint = state["setpoint"]
        controller._setpoint = (
            None
            if setpoint is None
            else cls._finite_float(setpoint, "setpoint")
        )
        controller._initialized = cls._strict_bool(
            state["initialized"], "initialized"
        )
        controller._filtered_error = cls._finite_float(
            state["filtered_error"], "filtered_error"
        )
        controller._variance = cls._finite_float(
            state["variance"], "variance"
        )
        controller._trend = cls._finite_float(state["trend"], "trend")
        controller._previous_error = cls._finite_float(
            state["previous_error"], "previous_error"
        )
        controller._blocked_high = cls._strict_bool(
            state["blocked_high"], "blocked_high"
        )
        controller._blocked_low = cls._strict_bool(
            state["blocked_low"], "blocked_low"
        )

        last_t = state["last_t"]
        if isinstance(last_t, bool) or not isinstance(last_t, int):
            raise ValueError("invalid last_t in snapshot")
        controller._last_t = last_t

        if not controller.min_limit <= controller.limit <= controller.max_limit:
            raise ValueError("snapshot limit is outside configured bounds")
        if controller._variance < 0.0:
            raise ValueError("snapshot variance is negative")

        return controller

    def _reset_signals(self):
        self._initialized = False
        self._filtered_error = 0.0
        self._variance = 0.0
        self._trend = 0.0
        self._previous_error = 0.0
        self._blocked_high = False
        self._blocked_low = False

    @staticmethod
    def _finite_float(value, name):
        if isinstance(value, bool):
            raise TypeError("%s must be a finite number" % name)
        try:
            result = float(value)
        except (TypeError, ValueError):
            raise TypeError("%s must be a finite number" % name)
        if not math.isfinite(result):
            raise ValueError("%s must be finite" % name)
        return result

    @staticmethod
    def _strict_bool(value, name):
        if not isinstance(value, bool):
            raise ValueError("invalid %s in snapshot" % name)
        return value
