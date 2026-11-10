import json
import math


class Controller:
    _VERSION = 1
    _ERROR_LIMIT = 4.0
    _DOWN_GAIN = 0.27
    _UP_GAIN = 0.10
    _TREND_GAIN = 0.025
    _TREND_ALPHA = 0.5
    _VAR_ALPHA = 0.35
    _VOLATILITY_GAIN = 0.10

    def __init__(self, config: dict):
        config = dict(config)
        self.min_limit = self._finite(config.get("min_limit", 1.0), "min_limit")
        self.max_limit = self._finite(config.get("max_limit", 200.0), "max_limit")
        initial = self._finite(config.get("initial_limit", 50.0), "initial_limit")

        if self.min_limit <= 0.0:
            raise ValueError("min_limit must be positive")
        if self.max_limit < self.min_limit:
            raise ValueError("max_limit must be at least min_limit")

        self.limit = self._clamp(initial, self.min_limit, self.max_limit)
        self._last_setpoint = None
        self._previous_error = None
        self._mean_error = None
        self._error_variance = 0.0
        self._trend = 0.0
        self._last_volatility = 0.0
        self._ticks = 0

    @staticmethod
    def _finite(value, name):
        value = float(value)
        if not math.isfinite(value):
            raise ValueError("%s must be finite" % name)
        return value

    @staticmethod
    def _clamp(value, lower, upper):
        return max(lower, min(upper, value))

    def _reset_history(self):
        self._previous_error = None
        self._mean_error = None
        self._error_variance = 0.0
        self._trend = 0.0
        self._last_volatility = 0.0

    def tick(self, obs: dict) -> dict:
        tick_index = int(obs["t"])
        latency = self._finite(obs["latency_ms"], "latency_ms")
        setpoint = self._finite(obs["setpoint_ms"], "setpoint_ms")
        arrival = self._finite(obs["arrival_rps"], "arrival_rps")
        admitted = self._finite(obs["admitted_rps"], "admitted_rps")

        if latency < 0.0:
            raise ValueError("latency_ms must be nonnegative")
        if setpoint <= 0.0:
            raise ValueError("setpoint_ms must be positive")
        if arrival < 0.0 or admitted < 0.0:
            raise ValueError("load values must be nonnegative")

        setpoint_changed = (
            self._last_setpoint is not None
            and setpoint != self._last_setpoint
        )
        if self._last_setpoint is None or setpoint_changed:
            self._reset_history()

        error = (latency - setpoint) / setpoint
        error = self._clamp(error, -self._ERROR_LIMIT, self._ERROR_LIMIT)

        if self._mean_error is None:
            self._mean_error = error
            self._error_variance = 0.0
        else:
            old_mean = self._mean_error
            alpha = self._VAR_ALPHA
            self._mean_error = old_mean + alpha * (error - old_mean)
            self._error_variance = (
                (1.0 - alpha)
                * (self._error_variance
                   + alpha * (error - old_mean) * (error - old_mean))
            )
            self._error_variance = max(0.0, self._error_variance)

        if self._previous_error is None:
            self._trend = 0.0
        else:
            difference = error - self._previous_error
            self._trend = (
                self._TREND_ALPHA * difference
                + (1.0 - self._TREND_ALPHA) * self._trend
            )

        volatility = math.sqrt(self._error_variance)
        volatility_change = volatility - self._last_volatility

        if error > 0.0:
            proportional_delta = -self.limit * self._DOWN_GAIN * error
        else:
            proportional_delta = -self.limit * self._UP_GAIN * error

        trend_delta = -self.limit * self._TREND_GAIN * self._trend
        volatility_delta = (
            -self.limit * self._VOLATILITY_GAIN * volatility_change
        )

        raw_limit = (
            self.limit
            + proportional_delta
            + trend_delta
            + volatility_delta
        )
        new_limit = self._clamp(raw_limit, self.min_limit, self.max_limit)

        if raw_limit >= self.max_limit:
            boundary = "max"
        elif raw_limit <= self.min_limit:
            boundary = "min"
        else:
            boundary = "none"

        self.limit = new_limit
        self._last_setpoint = setpoint
        self._previous_error = error
        self._last_volatility = volatility
        self._ticks += 1

        if boundary != "none":
            self._reset_history()

        demand_ratio = admitted / arrival if arrival > 0.0 else 0.0

        telemetry = {
            "version": self._VERSION,
            "tick": tick_index,
            "ticks_processed": self._ticks,
            "setpoint_ms": setpoint,
            "setpoint_changed": int(setpoint_changed),
            "error": error,
            "mean_error": (
                self._mean_error if self._mean_error is not None else 0.0
            ),
            "error_variance": self._error_variance,
            "trend": self._trend,
            "volatility": volatility,
            "state_volatility": self._last_volatility,
            "proportional_delta": proportional_delta,
            "trend_delta": trend_delta,
            "volatility_delta": volatility_delta,
            "raw_limit": raw_limit,
            "limit": self.limit,
            "boundary": boundary,
            "arrival_rps": arrival,
            "admitted_rps": admitted,
            "demand_ratio": demand_ratio,
        }
        return {"limit": self.limit, "telemetry": telemetry}

    def snapshot(self) -> str:
        payload = {
            "version": self._VERSION,
            "config": {
                "min_limit": self.min_limit,
                "max_limit": self.max_limit,
                "initial_limit": self.limit,
            },
            "state": {
                "limit": self.limit,
                "last_setpoint": self._last_setpoint,
                "previous_error": self._previous_error,
                "mean_error": self._mean_error,
                "error_variance": self._error_variance,
                "trend": self._trend,
                "last_volatility": self._last_volatility,
                "ticks": self._ticks,
            },
        }
        return json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )

    @classmethod
    def restore(cls, blob: str) -> "Controller":
        payload = json.loads(blob)
        if payload.get("version") != cls._VERSION:
            raise ValueError("unsupported snapshot version")

        controller = cls(payload["config"])
        state = payload["state"]

        controller.limit = cls._finite(state["limit"], "limit")
        if not controller.min_limit <= controller.limit <= controller.max_limit:
            raise ValueError("snapshot limit is out of bounds")

        def optional_float(name):
            value = state[name]
            return None if value is None else cls._finite(value, name)

        controller._last_setpoint = optional_float("last_setpoint")
        controller._previous_error = optional_float("previous_error")
        controller._mean_error = optional_float("mean_error")
        controller._error_variance = cls._finite(
            state["error_variance"], "error_variance"
        )
        controller._trend = cls._finite(state["trend"], "trend")
        controller._last_volatility = cls._finite(
            state["last_volatility"], "last_volatility"
        )
        controller._ticks = int(state["ticks"])

        if controller._error_variance < 0.0:
            raise ValueError("snapshot variance is negative")
        if controller._last_volatility < 0.0:
            raise ValueError("snapshot volatility is negative")
        if controller._ticks < 0:
            raise ValueError("snapshot tick count is negative")

        return controller
