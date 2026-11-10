import json
import math


class Controller:
    _VERSION = 1
    _UP_GAIN = 0.035
    _DOWN_GAIN = 0.12
    _TREND_GAIN = 0.015
    _VOLATILITY_GAIN = 0.015
    _EWMA_ALPHA = 0.45
    _MAX_ERROR = 4.0

    def __init__(self, config: dict):
        if not isinstance(config, dict):
            raise TypeError("config must be a dict")

        self.min_limit = self._finite(config.get("min_limit", 1.0), "min_limit")
        self.max_limit = self._finite(config.get("max_limit", 200.0), "max_limit")
        initial = self._finite(config.get("initial_limit", 50.0), "initial_limit")

        if self.min_limit <= 0.0:
            raise ValueError("min_limit must be positive")
        if self.max_limit < self.min_limit:
            raise ValueError("max_limit must be at least min_limit")
        if not self.min_limit <= initial <= self.max_limit:
            raise ValueError("initial_limit must be within configured bounds")

        self._limit = initial
        self._setpoint = None
        self._error_mean = 0.0
        self._error_variance = 0.0
        self._previous_error = 0.0
        self._samples = 0

    @staticmethod
    def _finite(value, name):
        if isinstance(value, bool):
            raise TypeError("%s must be a finite number" % name)
        try:
            result = float(value)
        except (TypeError, ValueError):
            raise TypeError("%s must be a finite number" % name)
        if not math.isfinite(result):
            raise ValueError("%s must be finite" % name)
        return result

    def _reset_history(self):
        self._error_mean = 0.0
        self._error_variance = 0.0
        self._previous_error = 0.0
        self._samples = 0

    def tick(self, obs: dict) -> dict:
        if not isinstance(obs, dict):
            raise TypeError("obs must be a dict")

        t = obs["t"]
        if isinstance(t, bool) or not isinstance(t, int):
            raise TypeError("t must be an int")

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

        setpoint_changed = self._setpoint is None or setpoint != self._setpoint
        if setpoint_changed:
            self._setpoint = setpoint
            self._reset_history()

        upper_saturated = self._limit >= self.max_limit
        if upper_saturated and not setpoint_changed:
            self._reset_history()

        error = (setpoint - latency) / setpoint
        error = max(-self._MAX_ERROR, min(self._MAX_ERROR, error))

        if self._samples == 0:
            trend = 0.0
            self._error_mean = error
            self._error_variance = 0.0
        else:
            trend = error - self._previous_error
            difference = error - self._error_mean
            alpha = self._EWMA_ALPHA
            self._error_mean += alpha * difference
            self._error_variance = (1.0 - alpha) * (
                self._error_variance + alpha * difference * difference
            )

        self._previous_error = error
        self._samples += 1

        volatility = math.sqrt(max(0.0, self._error_variance))
        gain = self._UP_GAIN if error >= 0.0 else self._DOWN_GAIN
        span = self.max_limit - self.min_limit

        proportional = span * gain * error
        trend_adjustment = span * self._TREND_GAIN * trend
        volatility_adjustment = (
            span * self._VOLATILITY_GAIN * volatility * abs(error)
        )
        requested_correction = (
            proportional + trend_adjustment - volatility_adjustment
        )

        old_limit = self._limit
        proposed = old_limit + requested_correction
        self._limit = max(self.min_limit, min(self.max_limit, proposed))
        applied_correction = self._limit - old_limit

        if self._limit >= self.max_limit:
            mode = "max"
        elif self._limit <= self.min_limit:
            mode = "min"
        elif error < 0.0:
            mode = "decrease"
        else:
            mode = "increase"

        telemetry = {
            "t": t,
            "setpoint_ms": setpoint,
            "error": error,
            "error_ewma": self._error_mean,
            "error_variance": self._error_variance,
            "volatility": volatility,
            "trend": trend,
            "requested_correction": requested_correction,
            "applied_correction": applied_correction,
            "sample_count": self._samples,
            "setpoint_changed": int(setpoint_changed),
            "saturated": int(
                self._limit <= self.min_limit or self._limit >= self.max_limit
            ),
            "mode": mode,
        }
        return {"limit": self._limit, "telemetry": telemetry}

    def snapshot(self) -> str:
        state = {
            "version": self._VERSION,
            "config": {
                "min_limit": self.min_limit,
                "max_limit": self.max_limit,
                "initial_limit": self._limit,
            },
            "state": {
                "limit": self._limit,
                "setpoint": self._setpoint,
                "error_mean": self._error_mean,
                "error_variance": self._error_variance,
                "previous_error": self._previous_error,
                "samples": self._samples,
            },
        }
        return json.dumps(
            state,
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
            raise ValueError("unsupported snapshot")

        config = data["config"]
        state = data["state"]
        controller = cls(config)

        controller._limit = cls._finite(state["limit"], "limit")
        controller._setpoint = (
            None
            if state["setpoint"] is None
            else cls._finite(state["setpoint"], "setpoint")
        )
        controller._error_mean = cls._finite(
            state["error_mean"], "error_mean"
        )
        controller._error_variance = cls._finite(
            state["error_variance"], "error_variance"
        )
        controller._previous_error = cls._finite(
            state["previous_error"], "previous_error"
        )

        samples = state["samples"]
        if isinstance(samples, bool) or not isinstance(samples, int) or samples < 0:
            raise ValueError("invalid sample count")
        controller._samples = samples

        if not controller.min_limit <= controller._limit <= controller.max_limit:
            raise ValueError("snapshot limit is outside configured bounds")
        if controller._error_variance < 0.0:
            raise ValueError("snapshot variance is negative")

        return controller
