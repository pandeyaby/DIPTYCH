import json
import math


class Controller:
    _VERSION = 1
    _UP_GAIN = 0.015
    _DOWN_GAIN = 0.040
    _TREND_GAIN = 0.75
    _VOLATILITY_GAIN = 0.003

    def __init__(self, config: dict):
        self.min_limit = float(config.get("min_limit", 1.0))
        self.max_limit = float(config.get("max_limit", 200.0))
        initial = float(config.get("initial_limit", 50.0))

        if not all(
            math.isfinite(value)
            for value in (self.min_limit, self.max_limit, initial)
        ):
            raise ValueError("controller limits must be finite")
        if self.min_limit <= 0.0 or self.max_limit < self.min_limit:
            raise ValueError("invalid controller limit range")

        self.limit = min(self.max_limit, max(self.min_limit, initial))
        self.setpoint = None
        self.previous_error = None
        self.sample_count = 0
        self.error_mean = 0.0
        self.error_m2 = 0.0
        self.last_t = -1

    def _reset_epoch(self) -> None:
        self.previous_error = None
        self.sample_count = 0
        self.error_mean = 0.0
        self.error_m2 = 0.0

    def _record_error(self, error: float) -> float:
        self.sample_count += 1
        delta = error - self.error_mean
        self.error_mean += delta / self.sample_count
        delta2 = error - self.error_mean
        self.error_m2 += delta * delta2

        if self.sample_count < 2:
            return 0.0
        variance = max(0.0, self.error_m2 / self.sample_count)
        return math.sqrt(variance)

    def tick(self, obs: dict) -> dict:
        t = int(obs["t"])
        latency = float(obs["latency_ms"])
        setpoint = float(obs["setpoint_ms"])
        arrival = float(obs["arrival_rps"])
        admitted = float(obs["admitted_rps"])

        if not all(
            math.isfinite(value)
            for value in (latency, setpoint, arrival, admitted)
        ):
            raise ValueError("observations must be finite")
        if latency < 0.0 or setpoint <= 0.0:
            raise ValueError("latency must be nonnegative and setpoint positive")

        setpoint_changed = self.setpoint is None or setpoint != self.setpoint
        if setpoint_changed:
            self.setpoint = setpoint
            self._reset_epoch()

        error = (setpoint - latency) / setpoint
        error = min(1.0, max(-1.0, error))

        if self.previous_error is None:
            trend = 0.0
            control_error = error
        else:
            trend = error - self.previous_error
            control_error = error + self._TREND_GAIN * trend

        volatility = self._record_error(error)
        gain = self._UP_GAIN if control_error >= 0.0 else self._DOWN_GAIN
        span = self.max_limit - self.min_limit

        proportional = span * gain * control_error
        volatility_penalty = span * self._VOLATILITY_GAIN * volatility
        raw_delta = proportional - volatility_penalty
        proposed = self.limit + raw_delta
        new_limit = min(self.max_limit, max(self.min_limit, proposed))

        if new_limit >= self.max_limit and proposed >= self.max_limit:
            saturation = "max"
        elif new_limit <= self.min_limit and proposed <= self.min_limit:
            saturation = "min"
        else:
            saturation = "none"

        self.limit = new_limit
        self.previous_error = error
        self.last_t = t

        samples_used = self.sample_count
        mean_error = self.error_mean

        # Discard hidden pressure at either boundary. In particular, time spent
        # at maximum cannot accumulate state that amplifies a later excursion.
        if saturation != "none":
            self._reset_epoch()

        utilization = 0.0
        if arrival > 0.0:
            utilization = min(1.0, max(0.0, admitted / arrival))

        telemetry = {
            "control_error": float(control_error),
            "error": float(error),
            "error_mean": float(mean_error),
            "gain": float(gain),
            "mode": "setpoint_reset" if setpoint_changed else "normal",
            "raw_delta": float(raw_delta),
            "sample_count": int(samples_used),
            "saturation": saturation,
            "setpoint_ms": float(setpoint),
            "trend": float(trend),
            "utilization": float(utilization),
            "volatility": float(volatility),
            "volatility_penalty": float(volatility_penalty),
        }
        return {"limit": float(self.limit), "telemetry": telemetry}

    def snapshot(self) -> str:
        state = {
            "version": self._VERSION,
            "config": {
                "initial_limit": self.limit,
                "max_limit": self.max_limit,
                "min_limit": self.min_limit,
            },
            "state": {
                "error_m2": self.error_m2,
                "error_mean": self.error_mean,
                "last_t": self.last_t,
                "limit": self.limit,
                "previous_error": self.previous_error,
                "sample_count": self.sample_count,
                "setpoint": self.setpoint,
            },
        }
        return json.dumps(
            state,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @classmethod
    def restore(cls, blob: str) -> "Controller":
        data = json.loads(blob)
        if data.get("version") != cls._VERSION:
            raise ValueError("unsupported snapshot version")

        controller = cls(data["config"])
        state = data["state"]
        controller.limit = float(state["limit"])
        controller.setpoint = (
            None if state["setpoint"] is None else float(state["setpoint"])
        )
        controller.previous_error = (
            None
            if state["previous_error"] is None
            else float(state["previous_error"])
        )
        controller.sample_count = int(state["sample_count"])
        controller.error_mean = float(state["error_mean"])
        controller.error_m2 = float(state["error_m2"])
        controller.last_t = int(state["last_t"])

        if not controller.min_limit <= controller.limit <= controller.max_limit:
            raise ValueError("snapshot limit is out of range")

        return controller
