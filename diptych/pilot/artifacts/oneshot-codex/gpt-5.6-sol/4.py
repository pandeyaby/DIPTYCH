import json
import math


class Controller:
    _ALPHA = 0.35
    _TREND_ALPHA = 0.50
    _TREND_GAIN = 1.50
    _VOLATILITY_GAIN = 0.12
    _UP_GAIN = 0.08
    _DOWN_GAIN = 0.32
    _MAX_UP_FRACTION = 0.10
    _MAX_DOWN_FRACTION = 0.35
    _VERSION = 1

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
        self.filtered_error = 0.0
        self.trend = 0.0
        self.error_variance = 0.0
        self.previous_error = 0.0
        self.last_setpoint = None
        self.initialized = False
        self.saturation_ticks = 0

    @staticmethod
    def _finite(value, name):
        value = float(value)
        if not math.isfinite(value):
            raise ValueError("%s must be finite" % name)
        return value

    @staticmethod
    def _clamp(value, lower, upper):
        return min(upper, max(lower, value))

    def tick(self, obs: dict) -> dict:
        latency = self._finite(obs["latency_ms"], "latency_ms")
        setpoint = self._finite(obs["setpoint_ms"], "setpoint_ms")
        arrival = self._finite(obs["arrival_rps"], "arrival_rps")
        admitted = self._finite(obs["admitted_rps"], "admitted_rps")
        tick_index = int(obs["t"])

        if setpoint <= 0.0:
            raise ValueError("setpoint_ms must be positive")
        if latency < 0.0:
            raise ValueError("latency_ms must be nonnegative")

        raw_error = (setpoint - latency) / setpoint
        setpoint_changed = (
            self.last_setpoint is not None and setpoint != self.last_setpoint
        )
        was_at_max = self.limit >= self.max_limit

        if not self.initialized or setpoint_changed or was_at_max:
            self.filtered_error = raw_error
            self.trend = 0.0
            self.error_variance = 0.0
            self.previous_error = raw_error
            self.initialized = True
            if setpoint_changed:
                mode = "setpoint_reset"
                self.saturation_ticks = 0
            elif was_at_max:
                mode = "saturated_reset"
            else:
                mode = "initial"
        else:
            old_mean = self.filtered_error
            difference = raw_error - old_mean
            self.filtered_error = old_mean + self._ALPHA * difference
            self.error_variance = (1.0 - self._ALPHA) * (
                self.error_variance + self._ALPHA * difference * difference
            )

            error_change = raw_error - self.previous_error
            self.trend += self._TREND_ALPHA * (error_change - self.trend)
            self.previous_error = raw_error
            mode = "normal"

        volatility = math.sqrt(max(0.0, self.error_variance))
        control_signal = (
            self.filtered_error
            + self._TREND_GAIN * self.trend
            - self._VOLATILITY_GAIN * volatility
        )

        old_limit = self.limit
        if control_signal >= 0.0:
            fraction = min(
                self._MAX_UP_FRACTION, self._UP_GAIN * control_signal
            )
        else:
            fraction = max(
                -self._MAX_DOWN_FRACTION, self._DOWN_GAIN * control_signal
            )

        requested_limit = old_limit * (1.0 + fraction)
        self.limit = self._clamp(
            requested_limit, self.min_limit, self.max_limit
        )

        if self.limit >= self.max_limit:
            self.saturation_ticks += 1
        else:
            self.saturation_ticks = 0

        self.last_setpoint = setpoint
        demand_ratio = admitted / arrival if arrival > 0.0 else 0.0

        telemetry = {
            "tick": tick_index,
            "mode": mode,
            "raw_error": raw_error,
            "filtered_error": self.filtered_error,
            "trend": self.trend,
            "error_variance": self.error_variance,
            "volatility": volatility,
            "control_signal": control_signal,
            "correction": self.limit - old_limit,
            "setpoint_ms": setpoint,
            "demand_ratio": demand_ratio,
            "saturation_ticks": self.saturation_ticks,
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
                "filtered_error": self.filtered_error,
                "trend": self.trend,
                "error_variance": self.error_variance,
                "previous_error": self.previous_error,
                "last_setpoint": self.last_setpoint,
                "initialized": self.initialized,
                "saturation_ticks": self.saturation_ticks,
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

        controller.limit = controller._finite(state["limit"], "limit")
        controller.filtered_error = controller._finite(
            state["filtered_error"], "filtered_error"
        )
        controller.trend = controller._finite(state["trend"], "trend")
        controller.error_variance = controller._finite(
            state["error_variance"], "error_variance"
        )
        controller.previous_error = controller._finite(
            state["previous_error"], "previous_error"
        )

        last_setpoint = state["last_setpoint"]
        controller.last_setpoint = (
            None
            if last_setpoint is None
            else controller._finite(last_setpoint, "last_setpoint")
        )
        controller.initialized = bool(state["initialized"])
        controller.saturation_ticks = int(state["saturation_ticks"])

        if not controller.min_limit <= controller.limit <= controller.max_limit:
            raise ValueError("snapshot limit is outside configured bounds")
        if controller.error_variance < 0.0:
            raise ValueError("snapshot variance is negative")
        if controller.saturation_ticks < 0:
            raise ValueError("snapshot saturation count is negative")

        return controller
