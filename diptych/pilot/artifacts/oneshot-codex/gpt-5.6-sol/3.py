import json
import math


class Controller:
    _VERSION = 1
    _ALPHA = 0.35
    _UP_GAIN = 0.09
    _DOWN_GAIN = 0.30
    _TREND_GAIN = 0.25
    _VOLATILITY_GAIN = 0.50

    def __init__(self, config: dict):
        self.min_limit = self._finite(config.get("min_limit", 1.0), "min_limit")
        self.max_limit = self._finite(config.get("max_limit", 200.0), "max_limit")
        initial = self._finite(config.get("initial_limit", 50.0), "initial_limit")

        if self.min_limit <= 0.0:
            raise ValueError("min_limit must be positive")
        if self.max_limit < self.min_limit:
            raise ValueError("max_limit must be >= min_limit")

        self.limit = self._clamp(initial)
        self.setpoint = None
        self.ewma_error = 0.0
        self.error_variance = 0.0
        self.trend = 0.0
        self.initialized = False
        self.saturated_max = self.limit >= self.max_limit
        self.last_t = -1

    @staticmethod
    def _finite(value, name):
        value = float(value)
        if not math.isfinite(value):
            raise ValueError("%s must be finite" % name)
        return value

    def _clamp(self, value):
        return min(self.max_limit, max(self.min_limit, value))

    def _reset_estimator(self):
        self.ewma_error = 0.0
        self.error_variance = 0.0
        self.trend = 0.0
        self.initialized = False

    def tick(self, obs: dict) -> dict:
        t = int(obs["t"])
        latency = self._finite(obs["latency_ms"], "latency_ms")
        setpoint = self._finite(obs["setpoint_ms"], "setpoint_ms")
        arrival = self._finite(obs["arrival_rps"], "arrival_rps")
        admitted = self._finite(obs["admitted_rps"], "admitted_rps")

        if latency < 0.0:
            raise ValueError("latency_ms must be nonnegative")
        if setpoint <= 0.0:
            raise ValueError("setpoint_ms must be positive")

        setpoint_changed = self.setpoint is None or setpoint != self.setpoint
        if setpoint_changed:
            self.setpoint = setpoint
            self._reset_estimator()
            self.saturated_max = False
        elif self.saturated_max:
            # Discard estimator state accumulated while the actuator was
            # pinned at its upper bound.
            self._reset_estimator()

        error = (latency - setpoint) / setpoint

        if not self.initialized:
            self.ewma_error = error
            self.error_variance = 0.0
            self.trend = 0.0
            self.initialized = True
        else:
            previous_mean = self.ewma_error
            difference = error - previous_mean
            self.ewma_error = previous_mean + self._ALPHA * difference
            self.error_variance = (
                (1.0 - self._ALPHA)
                * (self.error_variance + self._ALPHA * difference * difference)
            )
            self.trend = self.ewma_error - previous_mean

        volatility = math.sqrt(max(0.0, self.error_variance))
        signal = (
            self.ewma_error
            + self._TREND_GAIN * self.trend
            + self._VOLATILITY_GAIN * volatility
        )

        previous_limit = self.limit
        if signal > 0.0:
            requested_delta = -self._DOWN_GAIN * signal * previous_limit
        elif signal < 0.0:
            requested_delta = -self._UP_GAIN * signal * previous_limit
        else:
            requested_delta = 0.0

        self.limit = self._clamp(previous_limit + requested_delta)
        actual_delta = self.limit - previous_limit
        self.saturated_max = self.limit >= self.max_limit
        self.last_t = t

        if actual_delta > 0.0:
            direction = "up"
        elif actual_delta < 0.0:
            direction = "down"
        else:
            direction = "hold"

        telemetry = {
            "version": self._VERSION,
            "t": t,
            "setpoint_ms": setpoint,
            "latency_ms": latency,
            "arrival_rps": arrival,
            "admitted_rps": admitted,
            "normalized_error": error,
            "ewma_error": self.ewma_error,
            "error_variance": self.error_variance,
            "volatility": volatility,
            "trend": self.trend,
            "control_signal": signal,
            "requested_delta": requested_delta,
            "actual_delta": actual_delta,
            "direction": direction,
            "setpoint_changed": int(setpoint_changed),
            "saturated_max": int(self.saturated_max),
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
                "setpoint": self.setpoint,
                "ewma_error": self.ewma_error,
                "error_variance": self.error_variance,
                "trend": self.trend,
                "initialized": self.initialized,
                "saturated_max": self.saturated_max,
                "last_t": self.last_t,
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
        payload = json.loads(blob)
        if payload.get("version") != cls._VERSION:
            raise ValueError("unsupported snapshot version")

        controller = cls(payload["config"])
        state = payload["state"]

        controller.limit = controller._clamp(
            controller._finite(state["limit"], "limit")
        )
        controller.setpoint = (
            None
            if state["setpoint"] is None
            else controller._finite(state["setpoint"], "setpoint")
        )
        controller.ewma_error = controller._finite(
            state["ewma_error"], "ewma_error"
        )
        controller.error_variance = controller._finite(
            state["error_variance"], "error_variance"
        )
        controller.trend = controller._finite(state["trend"], "trend")
        controller.initialized = bool(state["initialized"])
        controller.saturated_max = bool(state["saturated_max"])
        controller.last_t = int(state["last_t"])
        return controller
