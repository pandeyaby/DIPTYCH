import json
import math


class Controller:
    _VERSION = 1
    _EMA_ALPHA = 0.35
    _UP_GAIN = 0.025
    _DOWN_GAIN = 0.075
    _UP_TREND_GAIN = 0.015
    _DOWN_TREND_GAIN = 0.045
    _VOLATILITY_GAIN = 0.08
    _MAX_UP_STEP = 0.06
    _MAX_DOWN_STEP = 0.18

    def __init__(self, config: dict):
        if not isinstance(config, dict):
            raise TypeError("config must be a dict")

        self.min_limit = self._number(config.get("min_limit", 1.0), "min_limit")
        self.max_limit = self._number(config.get("max_limit", 200.0), "max_limit")
        self.initial_limit = self._number(
            config.get("initial_limit", 50.0), "initial_limit"
        )

        if self.min_limit >= self.max_limit:
            raise ValueError("min_limit must be less than max_limit")
        if not self.min_limit <= self.initial_limit <= self.max_limit:
            raise ValueError("initial_limit must be within configured bounds")

        self._limit = self.initial_limit
        self._nominal_limit = self.initial_limit
        self._last_setpoint = None
        self._previous_error = 0.0
        self._error_ema = 0.0
        self._mean_error = 0.0
        self._error_m2 = 0.0
        self._sample_count = 0
        self._tick_count = 0
        self._epoch = 0
        self._has_observation = False
        self._saturated_max = False
        self._saturated_min = False

    @staticmethod
    def _number(value, name):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError("%s must be a finite number" % name)
        value = float(value)
        if not math.isfinite(value):
            raise ValueError("%s must be finite" % name)
        return value

    @staticmethod
    def _integer(value, name):
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError("%s must be an int" % name)
        return value

    @staticmethod
    def _clamp(value, low, high):
        return low if value < low else high if value > high else value

    def tick(self, obs: dict) -> dict:
        if not isinstance(obs, dict):
            raise TypeError("obs must be a dict")

        observed_t = self._integer(obs["t"], "t")
        latency = self._number(obs["latency_ms"], "latency_ms")
        setpoint = self._number(obs["setpoint_ms"], "setpoint_ms")
        arrival = self._number(obs["arrival_rps"], "arrival_rps")
        admitted = self._number(obs["admitted_rps"], "admitted_rps")

        if observed_t < 0:
            raise ValueError("t must be nonnegative")
        if latency < 0.0:
            raise ValueError("latency_ms must be nonnegative")
        if setpoint <= 0.0:
            raise ValueError("setpoint_ms must be positive")
        if arrival < 0.0 or admitted < 0.0:
            raise ValueError("load observations must be nonnegative")

        raw_error = (setpoint - latency) / setpoint
        error = self._clamp(raw_error, -4.0, 1.0)

        reset_reason = "none"
        setpoint_changed = (
            self._has_observation and setpoint != self._last_setpoint
        )

        if not self._has_observation:
            reset_reason = "initial"
        elif setpoint_changed:
            reset_reason = "setpoint"
            self._epoch += 1
        elif self._saturated_max:
            reset_reason = "max_saturation"
        elif self._saturated_min:
            reset_reason = "min_saturation"

        reset_dynamics = reset_reason != "none"

        if reset_dynamics:
            self._nominal_limit = self._limit
            self._previous_error = error
            self._error_ema = error
            self._mean_error = error
            self._error_m2 = 0.0
            self._sample_count = 1
            trend = 0.0
        else:
            trend = error - self._previous_error
            self._error_ema += self._EMA_ALPHA * (error - self._error_ema)

            self._sample_count += 1
            delta = error - self._mean_error
            self._mean_error += delta / self._sample_count
            delta2 = error - self._mean_error
            self._error_m2 += delta * delta2

        span = self.max_limit - self.min_limit

        if error >= 0.0:
            proportional = span * self._UP_GAIN * error
        else:
            proportional = span * self._DOWN_GAIN * error

        if trend >= 0.0:
            trend_term = span * self._UP_TREND_GAIN * trend
        else:
            trend_term = span * self._DOWN_TREND_GAIN * trend

        requested_correction = proportional + trend_term
        requested_correction = self._clamp(
            requested_correction,
            -span * self._MAX_DOWN_STEP,
            span * self._MAX_UP_STEP,
        )

        self._nominal_limit = self._clamp(
            self._nominal_limit + requested_correction,
            self.min_limit,
            self.max_limit,
        )

        variance = (
            self._error_m2 / self._sample_count
            if self._sample_count > 0
            else 0.0
        )
        if variance < 0.0:
            variance = 0.0

        volatility = math.sqrt(variance)
        volatility_reserve = span * self._VOLATILITY_GAIN * volatility

        old_limit = self._limit
        self._limit = self._clamp(
            self._nominal_limit - volatility_reserve,
            self.min_limit,
            self.max_limit,
        )

        self._saturated_max = self._limit >= self.max_limit
        self._saturated_min = self._limit <= self.min_limit
        saturation = (
            "max"
            if self._saturated_max
            else "min"
            if self._saturated_min
            else "none"
        )

        self._last_setpoint = setpoint
        self._previous_error = error
        self._has_observation = True
        self._tick_count += 1

        telemetry = {
            "tick": observed_t,
            "tick_count": self._tick_count,
            "epoch": self._epoch,
            "setpoint_ms": setpoint,
            "arrival_rps": arrival,
            "admitted_rps": admitted,
            "raw_error": raw_error,
            "error": error,
            "error_ema": self._error_ema,
            "error_trend": trend,
            "error_variance": variance,
            "proportional_term": proportional,
            "trend_term": trend_term,
            "requested_correction": requested_correction,
            "volatility_reserve": volatility_reserve,
            "nominal_limit": self._nominal_limit,
            "adjustment": self._limit - old_limit,
            "limit": self._limit,
            "sample_count": self._sample_count,
            "saturation": saturation,
            "reset_reason": reset_reason,
        }
        return {"limit": self._limit, "telemetry": telemetry}

    def snapshot(self) -> str:
        payload = {
            "version": self._VERSION,
            "config": {
                "min_limit": self.min_limit,
                "max_limit": self.max_limit,
                "initial_limit": self.initial_limit,
            },
            "state": {
                "limit": self._limit,
                "nominal_limit": self._nominal_limit,
                "last_setpoint": self._last_setpoint,
                "previous_error": self._previous_error,
                "error_ema": self._error_ema,
                "mean_error": self._mean_error,
                "error_m2": self._error_m2,
                "sample_count": self._sample_count,
                "tick_count": self._tick_count,
                "epoch": self._epoch,
                "has_observation": self._has_observation,
                "saturated_max": self._saturated_max,
                "saturated_min": self._saturated_min,
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
        if not isinstance(blob, str):
            raise TypeError("blob must be a string")

        try:
            payload = json.loads(blob)
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid snapshot") from exc

        if not isinstance(payload, dict) or payload.get("version") != cls._VERSION:
            raise ValueError("unsupported snapshot version")

        try:
            controller = cls(payload["config"])
            state = payload["state"]

            controller._limit = cls._number(state["limit"], "limit")
            controller._nominal_limit = cls._number(
                state["nominal_limit"], "nominal_limit"
            )

            last_setpoint = state["last_setpoint"]
            controller._last_setpoint = (
                None
                if last_setpoint is None
                else cls._number(last_setpoint, "last_setpoint")
            )
            controller._previous_error = cls._number(
                state["previous_error"], "previous_error"
            )
            controller._error_ema = cls._number(
                state["error_ema"], "error_ema"
            )
            controller._mean_error = cls._number(
                state["mean_error"], "mean_error"
            )
            controller._error_m2 = cls._number(state["error_m2"], "error_m2")
            controller._sample_count = cls._integer(
                state["sample_count"], "sample_count"
            )
            controller._tick_count = cls._integer(
                state["tick_count"], "tick_count"
            )
            controller._epoch = cls._integer(state["epoch"], "epoch")

            for key, attribute in (
                ("has_observation", "_has_observation"),
                ("saturated_max", "_saturated_max"),
                ("saturated_min", "_saturated_min"),
            ):
                value = state[key]
                if not isinstance(value, bool):
                    raise TypeError("%s must be a bool" % key)
                setattr(controller, attribute, value)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("invalid snapshot state") from exc

        if not controller.min_limit <= controller._limit <= controller.max_limit:
            raise ValueError("snapshot limit is outside configured bounds")
        if not controller.min_limit <= controller._nominal_limit <= controller.max_limit:
            raise ValueError("snapshot nominal limit is outside configured bounds")
        if (
            controller._sample_count < 0
            or controller._tick_count < 0
            or controller._epoch < 0
            or controller._error_m2 < 0.0
        ):
            raise ValueError("snapshot contains invalid state")

        return controller
