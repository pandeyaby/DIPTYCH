import json
import math


class Controller:
    _VERSION = 1

    def __init__(self, config: dict):
        self.min_limit = self._finite(config.get("min_limit", 1.0), "min_limit")
        self.max_limit = self._finite(config.get("max_limit", 200.0), "max_limit")
        self.initial_limit = self._finite(
            config.get("initial_limit", 50.0), "initial_limit"
        )

        if self.min_limit > self.max_limit:
            raise ValueError("min_limit must not exceed max_limit")
        if not self.min_limit <= self.initial_limit <= self.max_limit:
            raise ValueError("initial_limit must be within configured bounds")

        self.limit = self.initial_limit
        self.last_setpoint = None
        self.filtered_error = 0.0
        self.previous_error = 0.0
        self.sample_count = 0
        self.error_mean = 0.0
        self.error_m2 = 0.0
        self.saturation_side = 0
        self.last_tick = -1

    @staticmethod
    def _finite(value, name):
        value = float(value)
        if not math.isfinite(value):
            raise ValueError("%s must be finite" % name)
        return value

    def _reset_dynamics(self):
        self.filtered_error = 0.0
        self.previous_error = 0.0
        self.sample_count = 0
        self.error_mean = 0.0
        self.error_m2 = 0.0

    def tick(self, obs: dict) -> dict:
        tick_index = int(obs["t"])
        latency = self._finite(obs["latency_ms"], "latency_ms")
        setpoint = self._finite(obs["setpoint_ms"], "setpoint_ms")
        arrival = self._finite(obs["arrival_rps"], "arrival_rps")
        admitted = self._finite(obs["admitted_rps"], "admitted_rps")

        if setpoint <= 0.0:
            raise ValueError("setpoint_ms must be positive")

        if self.saturation_side:
            self._reset_dynamics()

        setpoint_reset = int(
            self.last_setpoint is not None and setpoint != self.last_setpoint
        )
        if self.last_setpoint is None or setpoint_reset:
            self._reset_dynamics()

        error = (setpoint - latency) / setpoint
        error = max(-4.0, min(4.0, error))

        if self.sample_count == 0:
            self.filtered_error = error
            trend = 0.0
        else:
            self.filtered_error = 0.30 * self.filtered_error + 0.70 * error
            trend = error - self.previous_error

        self.sample_count += 1
        mean_delta = error - self.error_mean
        self.error_mean += mean_delta / self.sample_count
        self.error_m2 += mean_delta * (error - self.error_mean)
        variance = max(0.0, self.error_m2 / self.sample_count)
        volatility = math.sqrt(variance)

        signal = 0.90 * error + 0.10 * self.filtered_error
        gain = 0.32 if signal < 0.0 else 0.04
        span = self.max_limit - self.min_limit

        proportional = span * gain * signal
        trend_adjustment = span * 0.06 * trend
        volatility_penalty = span * 0.015 * volatility
        correction = proportional + trend_adjustment - volatility_penalty

        proposed = self.limit + correction
        new_limit = max(self.min_limit, min(self.max_limit, proposed))

        if new_limit >= self.max_limit:
            self.saturation_side = 1
        elif new_limit <= self.min_limit:
            self.saturation_side = -1
        else:
            self.saturation_side = 0

        if correction > 0.0:
            mode = "increase"
        elif correction < 0.0:
            mode = "decrease"
        else:
            mode = "hold"

        utilization = admitted / arrival if arrival > 0.0 else 0.0

        self.limit = new_limit
        self.previous_error = error
        self.last_setpoint = setpoint
        self.last_tick = tick_index

        telemetry = {
            "t": tick_index,
            "mode": mode,
            "error": error,
            "filtered_error": self.filtered_error,
            "trend": trend,
            "volatility": volatility,
            "proportional": proportional,
            "trend_adjustment": trend_adjustment,
            "volatility_penalty": volatility_penalty,
            "correction": correction,
            "utilization": utilization,
            "sample_count": self.sample_count,
            "saturation_side": self.saturation_side,
            "setpoint_reset": setpoint_reset,
            "setpoint_ms": setpoint,
        }
        return {"limit": self.limit, "telemetry": telemetry}

    def snapshot(self) -> str:
        state = {
            "version": self._VERSION,
            "config": {
                "min_limit": self.min_limit,
                "max_limit": self.max_limit,
                "initial_limit": self.initial_limit,
            },
            "state": {
                "limit": self.limit,
                "last_setpoint": self.last_setpoint,
                "filtered_error": self.filtered_error,
                "previous_error": self.previous_error,
                "sample_count": self.sample_count,
                "error_mean": self.error_mean,
                "error_m2": self.error_m2,
                "saturation_side": self.saturation_side,
                "last_tick": self.last_tick,
            },
        }
        return json.dumps(
            state, sort_keys=True, separators=(",", ":"), allow_nan=False
        )

    @classmethod
    def restore(cls, blob: str) -> "Controller":
        data = json.loads(blob)
        if data.get("version") != cls._VERSION:
            raise ValueError("unsupported snapshot version")

        controller = cls(data["config"])
        state = data["state"]

        controller.limit = cls._finite(state["limit"], "limit")
        last_setpoint = state["last_setpoint"]
        controller.last_setpoint = (
            None
            if last_setpoint is None
            else cls._finite(last_setpoint, "last_setpoint")
        )
        controller.filtered_error = cls._finite(
            state["filtered_error"], "filtered_error"
        )
        controller.previous_error = cls._finite(
            state["previous_error"], "previous_error"
        )
        controller.sample_count = int(state["sample_count"])
        controller.error_mean = cls._finite(state["error_mean"], "error_mean")
        controller.error_m2 = cls._finite(state["error_m2"], "error_m2")
        controller.saturation_side = int(state["saturation_side"])
        controller.last_tick = int(state["last_tick"])

        if not controller.min_limit <= controller.limit <= controller.max_limit:
            raise ValueError("snapshot limit is outside configured bounds")
        if controller.sample_count < 0:
            raise ValueError("invalid snapshot sample count")
        if controller.saturation_side not in (-1, 0, 1):
            raise ValueError("invalid snapshot saturation state")

        return controller
