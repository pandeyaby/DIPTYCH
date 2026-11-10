import json
import math


class Controller:
    _VERSION = 1
    _ERROR_ALPHA = 0.35
    _VARIANCE_ALPHA = 0.25
    _TREND_ALPHA = 0.45
    _TREND_GAIN = 3.0
    _UP_GAIN = 0.07
    _DOWN_GAIN = 0.24
    _VOLATILITY_GAIN = 0.12
    _MAX_INCREASE = 0.15
    _MAX_DECREASE = -0.50

    def __init__(self, config: dict):
        self.min_limit = self._finite(config.get("min_limit", 1.0), "min_limit")
        self.max_limit = self._finite(config.get("max_limit", 200.0), "max_limit")
        initial = self._finite(config.get("initial_limit", 50.0), "initial_limit")

        if self.min_limit <= 0.0:
            raise ValueError("min_limit must be positive")
        if self.max_limit < self.min_limit:
            raise ValueError("max_limit must be at least min_limit")

        self.limit = min(self.max_limit, max(self.min_limit, initial))
        self.last_setpoint = None
        self.filtered_error = 0.0
        self.trend = 0.0
        self.variance = 0.0
        self.last_error = None
        self.samples = 0

    @staticmethod
    def _finite(value, name):
        value = float(value)
        if not math.isfinite(value):
            raise ValueError("%s must be finite" % name)
        return value

    def _clear_filters(self):
        self.filtered_error = 0.0
        self.trend = 0.0
        self.variance = 0.0
        self.last_error = None
        self.samples = 0

    def tick(self, obs: dict) -> dict:
        latency = self._finite(obs["latency_ms"], "latency_ms")
        setpoint = self._finite(obs["setpoint_ms"], "setpoint_ms")
        arrival = self._finite(obs["arrival_rps"], "arrival_rps")
        admitted = self._finite(obs["admitted_rps"], "admitted_rps")
        tick_index = int(obs["t"])

        if setpoint <= 0.0:
            raise ValueError("setpoint_ms must be positive")

        setpoint_changed = (
            self.last_setpoint is not None and setpoint != self.last_setpoint
        )
        if self.last_setpoint is None or setpoint_changed:
            self._clear_filters()

        self.last_setpoint = setpoint
        raw_error = (setpoint - latency) / setpoint
        error = min(1.0, max(-4.0, raw_error))

        if self.last_error is None:
            self.filtered_error = error
            self.trend = 0.0
            self.variance = 0.0
            self.samples = 1
        else:
            previous_filtered = self.filtered_error
            difference = error - self.last_error
            innovation = error - previous_filtered

            self.filtered_error = (
                self._ERROR_ALPHA * error
                + (1.0 - self._ERROR_ALPHA) * previous_filtered
            )
            self.trend = (
                self._TREND_ALPHA * difference
                + (1.0 - self._TREND_ALPHA) * self.trend
            )
            self.variance = (
                (1.0 - self._VARIANCE_ALPHA) * self.variance
                + self._VARIANCE_ALPHA * innovation * innovation
            )
            self.samples += 1

        self.last_error = error
        volatility = math.sqrt(max(0.0, self.variance))
        signal = self.filtered_error + self._TREND_GAIN * self.trend

        if signal >= 0.0:
            fractional_change = self._UP_GAIN * signal
        else:
            fractional_change = self._DOWN_GAIN * signal

        fractional_change -= self._VOLATILITY_GAIN * volatility
        fractional_change = min(
            self._MAX_INCREASE,
            max(self._MAX_DECREASE, fractional_change),
        )

        previous_limit = self.limit
        proposed_limit = previous_limit * (1.0 + fractional_change)
        new_limit = min(self.max_limit, max(self.min_limit, proposed_limit))

        saturated = 0
        if proposed_limit > self.max_limit:
            saturated = 1
        elif proposed_limit < self.min_limit:
            saturated = -1

        self.limit = new_limit

        # Discard pressure that could otherwise accumulate behind a hard bound.
        if (saturated == 1 and fractional_change > 0.0) or (
            saturated == -1 and fractional_change < 0.0
        ):
            self._clear_filters()

        actual_adjustment = self.limit - previous_limit
        if actual_adjustment > 0.0:
            mode = "increase"
        elif actual_adjustment < 0.0:
            mode = "decrease"
        else:
            mode = "hold"

        telemetry = {
            "tick": tick_index,
            "mode": mode,
            "error": error,
            "raw_error": raw_error,
            "filtered_error": self.filtered_error,
            "trend": self.trend,
            "variance": self.variance,
            "volatility": volatility,
            "signal": signal,
            "fractional_change": fractional_change,
            "adjustment": actual_adjustment,
            "unclamped_limit": proposed_limit,
            "saturated": saturated,
            "setpoint_changed": int(setpoint_changed),
            "setpoint_ms": setpoint,
            "arrival_rps": arrival,
            "admitted_rps": admitted,
            "samples": self.samples,
        }
        return {"limit": self.limit, "telemetry": telemetry}

    def snapshot(self) -> str:
        state = {
            "version": self._VERSION,
            "config": {
                "min_limit": self.min_limit,
                "max_limit": self.max_limit,
                "initial_limit": self.limit,
            },
            "state": {
                "limit": self.limit,
                "last_setpoint": self.last_setpoint,
                "filtered_error": self.filtered_error,
                "trend": self.trend,
                "variance": self.variance,
                "last_error": self.last_error,
                "samples": self.samples,
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
        data = json.loads(blob)
        if data.get("version") != cls._VERSION:
            raise ValueError("unsupported snapshot version")

        controller = cls(data["config"])
        state = data["state"]

        controller.limit = cls._finite(state["limit"], "limit")
        controller.filtered_error = cls._finite(
            state["filtered_error"], "filtered_error"
        )
        controller.trend = cls._finite(state["trend"], "trend")
        controller.variance = cls._finite(state["variance"], "variance")
        controller.samples = int(state["samples"])

        last_setpoint = state["last_setpoint"]
        controller.last_setpoint = (
            None
            if last_setpoint is None
            else cls._finite(last_setpoint, "last_setpoint")
        )

        last_error = state["last_error"]
        controller.last_error = (
            None if last_error is None else cls._finite(last_error, "last_error")
        )

        if not controller.min_limit <= controller.limit <= controller.max_limit:
            raise ValueError("snapshot limit is outside configured bounds")
        if controller.variance < 0.0 or controller.samples < 0:
            raise ValueError("invalid snapshot state")

        return controller
