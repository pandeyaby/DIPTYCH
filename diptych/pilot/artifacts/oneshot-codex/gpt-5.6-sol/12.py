import json
import math


class Controller:
    _VERSION = 1
    _UP_GAIN = 0.24
    _DOWN_GAIN = 0.62
    _ERROR_ALPHA = 0.45
    _TREND_ALPHA = 0.50
    _TREND_WEIGHT = 1.50
    _VAR_ALPHA = 0.20
    _VOLATILITY_WEIGHT = 0.10

    def __init__(self, config: dict):
        if not isinstance(config, dict):
            raise TypeError("config must be a dict")

        self.min_limit = self._finite(config.get("min_limit", 1.0), "min_limit")
        self.max_limit = self._finite(config.get("max_limit", 200.0), "max_limit")
        initial = self._finite(config.get("initial_limit", 50.0), "initial_limit")

        if self.min_limit <= 0.0:
            raise ValueError("min_limit must be positive")
        if self.max_limit < self.min_limit:
            raise ValueError("max_limit must be >= min_limit")

        self.limit = self._clamp(initial)
        self._scale = min(20.0, max(1.0, math.sqrt(self.limit)))
        self._setpoint = None
        self._filtered_error = 0.0
        self._error_variance = 0.0
        self._trend = 0.0
        self._previous_error = None
        self._sample_count = 0
        self._last_t = -1

    @staticmethod
    def _finite(value, name):
        value = float(value)
        if not math.isfinite(value):
            raise ValueError("%s must be finite" % name)
        return value

    def _clamp(self, value):
        return min(self.max_limit, max(self.min_limit, value))

    def _reset_epoch(self, setpoint):
        self._setpoint = setpoint
        self._filtered_error = 0.0
        self._error_variance = 0.0
        self._trend = 0.0
        self._previous_error = None
        self._sample_count = 0

    def tick(self, obs: dict) -> dict:
        if not isinstance(obs, dict):
            raise TypeError("obs must be a dict")

        t = int(obs["t"])
        latency = self._finite(obs["latency_ms"], "latency_ms")
        setpoint = self._finite(obs["setpoint_ms"], "setpoint_ms")
        arrival = self._finite(obs["arrival_rps"], "arrival_rps")
        admitted = self._finite(obs["admitted_rps"], "admitted_rps")

        if t < 0:
            raise ValueError("t must be nonnegative")
        if setpoint <= 0.0:
            raise ValueError("setpoint_ms must be positive")
        if latency < 0.0 or arrival < 0.0 or admitted < 0.0:
            raise ValueError("latency and load values must be nonnegative")

        reset = self._setpoint is None or setpoint != self._setpoint
        if reset:
            self._reset_epoch(setpoint)

        raw_error = (setpoint - latency) / setpoint
        at_max_before = self.limit >= self.max_limit

        if self._previous_error is None:
            self._filtered_error = raw_error
            self._error_variance = 0.0
            self._trend = 0.0
        else:
            old_filtered = self._filtered_error
            innovation = raw_error - old_filtered
            self._filtered_error = (
                old_filtered
                + self._ERROR_ALPHA * innovation
            )
            self._error_variance = (
                (1.0 - self._VAR_ALPHA) * self._error_variance
                + self._VAR_ALPHA * innovation * innovation
            )
            change = raw_error - self._previous_error
            self._trend = (
                (1.0 - self._TREND_ALPHA) * self._trend
                + self._TREND_ALPHA * change
            )

        volatility = math.sqrt(max(0.0, self._error_variance))
        drive = self._filtered_error + self._TREND_WEIGHT * self._trend
        volatility_penalty = self._VOLATILITY_WEIGHT * volatility

        # A fresh overload excursion at the upper bound is handled solely from
        # its current error, preventing hidden state accumulated at saturation.
        saturation_excursion = at_max_before and raw_error < 0.0
        if saturation_excursion:
            drive = raw_error
            volatility_penalty = 0.0

        gain = self._UP_GAIN if drive >= 0.0 else self._DOWN_GAIN
        requested_delta = self._scale * (
            gain * drive - volatility_penalty
        )

        old_limit = self.limit
        self.limit = self._clamp(old_limit + requested_delta)
        applied_delta = self.limit - old_limit

        self._previous_error = raw_error
        self._sample_count += 1
        self._last_t = t

        if arrival > 0.0:
            demand_ratio = admitted / arrival
        else:
            demand_ratio = 1.0

        telemetry = {
            "t": t,
            "limit": self.limit,
            "raw_error": raw_error,
            "filtered_error": self._filtered_error,
            "error_variance": self._error_variance,
            "volatility": volatility,
            "trend": self._trend,
            "drive": drive,
            "gain": gain,
            "volatility_penalty": volatility_penalty,
            "requested_delta": requested_delta,
            "applied_delta": applied_delta,
            "setpoint_ms": setpoint,
            "demand_ratio": demand_ratio,
            "sample_count": self._sample_count,
            "setpoint_reset": int(reset),
            "at_min": int(self.limit <= self.min_limit),
            "at_max": int(self.limit >= self.max_limit),
            "saturation_excursion": int(saturation_excursion),
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
                "scale": self._scale,
                "setpoint": self._setpoint,
                "filtered_error": self._filtered_error,
                "error_variance": self._error_variance,
                "trend": self._trend,
                "previous_error": self._previous_error,
                "sample_count": self._sample_count,
                "last_t": self._last_t,
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
        controller._scale = controller._finite(state["scale"], "scale")
        controller._setpoint = state["setpoint"]
        controller._filtered_error = controller._finite(
            state["filtered_error"], "filtered_error"
        )
        controller._error_variance = controller._finite(
            state["error_variance"], "error_variance"
        )
        controller._trend = controller._finite(state["trend"], "trend")

        previous = state["previous_error"]
        controller._previous_error = (
            None
            if previous is None
            else controller._finite(previous, "previous_error")
        )
        controller._sample_count = int(state["sample_count"])
        controller._last_t = int(state["last_t"])

        if controller._setpoint is not None:
            controller._setpoint = controller._finite(
                controller._setpoint, "setpoint"
            )
        if not controller.min_limit <= controller.limit <= controller.max_limit:
            raise ValueError("snapshot limit is out of bounds")
        if controller._scale <= 0.0:
            raise ValueError("snapshot scale must be positive")
        if controller._sample_count < 0:
            raise ValueError("snapshot sample_count is invalid")

        return controller
