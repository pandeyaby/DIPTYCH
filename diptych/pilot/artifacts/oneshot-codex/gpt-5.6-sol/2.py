import json
import math


class Controller:
    _VERSION = 1
    _UP_GAIN = 0.06
    _DOWN_GAIN = 0.30
    _TREND_GAIN = 0.08
    _EWMA_ALPHA = 0.25
    _VOLATILITY_GAIN = 0.10

    def __init__(self, config: dict):
        config = dict(config)
        self.min_limit = self._finite(config.get("min_limit", 1.0), "min_limit")
        self.max_limit = self._finite(config.get("max_limit", 200.0), "max_limit")
        initial = self._finite(config.get("initial_limit", 50.0), "initial_limit")

        if self.min_limit > self.max_limit:
            raise ValueError("min_limit must not exceed max_limit")

        self.initial_limit = self._clamp(initial)
        self._scale = max(1.0, 0.25 * (self.max_limit - self.min_limit))
        self._core_limit = self.initial_limit
        self._limit = self.initial_limit

        self._last_setpoint = None
        self._have_error = False
        self._previous_error = 0.0
        self._mean_error = 0.0
        self._variance = 0.0
        self._ticks = 0

    @staticmethod
    def _finite(value, name):
        try:
            value = float(value)
        except (TypeError, ValueError):
            raise ValueError("%s must be a finite number" % name)
        if not math.isfinite(value):
            raise ValueError("%s must be a finite number" % name)
        return value

    def _clamp(self, value):
        return min(self.max_limit, max(self.min_limit, value))

    def _reset_observation_state(self):
        self._have_error = False
        self._previous_error = 0.0
        self._mean_error = 0.0
        self._variance = 0.0

    def tick(self, obs: dict) -> dict:
        latency = self._finite(obs["latency_ms"], "latency_ms")
        setpoint = self._finite(obs["setpoint_ms"], "setpoint_ms")
        self._finite(obs["arrival_rps"], "arrival_rps")
        self._finite(obs["admitted_rps"], "admitted_rps")

        if setpoint <= 0.0:
            raise ValueError("setpoint_ms must be positive")
        if latency < 0.0:
            raise ValueError("latency_ms must be nonnegative")

        setpoint_changed = (
            self._last_setpoint is not None and setpoint != self._last_setpoint
        )
        if self._last_setpoint is None or setpoint_changed:
            self._reset_observation_state()
        self._last_setpoint = setpoint

        error = (setpoint - latency) / setpoint

        if self._have_error:
            trend = error - self._previous_error
            old_mean = self._mean_error
            alpha = self._EWMA_ALPHA
            self._mean_error = old_mean + alpha * (error - old_mean)
            self._variance = (1.0 - alpha) * (
                self._variance + alpha * (error - old_mean) ** 2
            )
        else:
            trend = 0.0
            self._mean_error = error
            self._variance = 0.0
            self._have_error = True

        self._previous_error = error

        gain = self._UP_GAIN if error >= 0.0 else self._DOWN_GAIN
        correction = self._scale * (
            gain * error + self._TREND_GAIN * trend
        )
        proposed_core = self._core_limit + correction
        self._core_limit = self._clamp(proposed_core)

        outward_max = proposed_core >= self.max_limit and error >= 0.0
        outward_min = proposed_core <= self.min_limit and error <= 0.0

        if outward_max or outward_min:
            self._reset_observation_state()
            volatility = 0.0
            reserve = 0.0
            trend_state = 0.0
            mean_state = 0.0
            variance_state = 0.0
        else:
            variance_state = max(0.0, self._variance)
            volatility = math.sqrt(variance_state)
            reserve = self._scale * self._VOLATILITY_GAIN * volatility
            trend_state = trend
            mean_state = self._mean_error

        self._limit = self._clamp(self._core_limit - reserve)
        self._ticks += 1

        telemetry = {
            "at_max": int(self._limit >= self.max_limit),
            "at_min": int(self._limit <= self.min_limit),
            "core_limit": float(self._core_limit),
            "error_ewma": float(mean_state),
            "error_norm": float(error),
            "error_trend": float(trend_state),
            "regime": "reset" if setpoint_changed else "normal",
            "setpoint_ms": float(setpoint),
            "variance": float(variance_state),
            "volatility_reserve": float(reserve),
        }
        return {"limit": float(self._limit), "telemetry": telemetry}

    def snapshot(self) -> str:
        payload = {
            "config": {
                "initial_limit": self.initial_limit,
                "max_limit": self.max_limit,
                "min_limit": self.min_limit,
            },
            "state": {
                "core_limit": self._core_limit,
                "have_error": self._have_error,
                "last_setpoint": self._last_setpoint,
                "limit": self._limit,
                "mean_error": self._mean_error,
                "previous_error": self._previous_error,
                "ticks": self._ticks,
                "variance": self._variance,
            },
            "version": self._VERSION,
        }
        return json.dumps(
            payload,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @classmethod
    def restore(cls, blob: str) -> "Controller":
        payload = json.loads(blob)
        if payload.get("version") != cls._VERSION:
            raise ValueError("unsupported snapshot version")

        controller = cls(payload["config"])
        state = payload["state"]

        controller._core_limit = float(state["core_limit"])
        controller._have_error = bool(state["have_error"])
        last_setpoint = state["last_setpoint"]
        controller._last_setpoint = (
            None if last_setpoint is None else float(last_setpoint)
        )
        controller._limit = float(state["limit"])
        controller._mean_error = float(state["mean_error"])
        controller._previous_error = float(state["previous_error"])
        controller._ticks = int(state["ticks"])
        controller._variance = float(state["variance"])
        return controller
