import json
import math


class Controller:
    _UP_GAIN = 0.07
    _DOWN_GAIN = 0.50
    _TREND_GAIN = 0.06
    _VOLATILITY_GAIN = 0.18
    _SNAPSHOT_VERSION = 1

    def __init__(self, config: dict):
        config = dict(config)
        self.min_limit = self._number(config.get("min_limit", 1.0), "min_limit")
        self.max_limit = self._number(config.get("max_limit", 200.0), "max_limit")
        initial = self._number(config.get("initial_limit", 50.0), "initial_limit")

        if self.min_limit <= 0.0:
            raise ValueError("min_limit must be positive")
        if self.max_limit < self.min_limit:
            raise ValueError("max_limit must be at least min_limit")

        initial = min(self.max_limit, max(self.min_limit, initial))
        self.initial_limit = initial
        self._log_min = math.log(self.min_limit)
        self._log_max = math.log(self.max_limit)
        self._log_limit = math.log(initial)

        self._setpoint = None
        self._count = 0
        self._mean_error = 0.0
        self._m2_error = 0.0
        self._previous_error = None

    @staticmethod
    def _number(value, name):
        if isinstance(value, bool):
            raise TypeError("%s must be a finite number" % name)
        value = float(value)
        if not math.isfinite(value):
            raise ValueError("%s must be finite" % name)
        return value

    def _clear_history(self):
        self._count = 0
        self._mean_error = 0.0
        self._m2_error = 0.0
        self._previous_error = None

    def tick(self, obs: dict) -> dict:
        tick_index = int(obs["t"])
        latency = self._number(obs["latency_ms"], "latency_ms")
        setpoint = self._number(obs["setpoint_ms"], "setpoint_ms")
        arrival = self._number(obs["arrival_rps"], "arrival_rps")
        admitted = self._number(obs["admitted_rps"], "admitted_rps")

        if latency < 0.0:
            raise ValueError("latency_ms must be nonnegative")
        if setpoint <= 0.0:
            raise ValueError("setpoint_ms must be positive")

        if self._setpoint is None or setpoint != self._setpoint:
            self._setpoint = setpoint
            self._clear_history()

        old_limit = math.exp(self._log_limit)
        error = (setpoint - latency) / setpoint
        error = min(1.0, max(-1.0, error))

        old_volatility = (
            math.sqrt(max(0.0, self._m2_error / self._count))
            if self._count
            else 0.0
        )

        self._count += 1
        delta = error - self._mean_error
        self._mean_error += delta / self._count
        delta2 = error - self._mean_error
        self._m2_error += delta * delta2

        volatility = math.sqrt(max(0.0, self._m2_error / self._count))
        volatility_adjustment = (
            -self._VOLATILITY_GAIN * (volatility - old_volatility)
        )

        if error >= 0.0:
            proportional = self._UP_GAIN * error
        else:
            proportional = self._DOWN_GAIN * error

        if self._previous_error is None:
            trend = 0.0
        else:
            trend = self._TREND_GAIN * (error - self._previous_error)

        log_adjustment = proportional + trend + volatility_adjustment
        proposed_log_limit = self._log_limit + log_adjustment
        self._log_limit = min(
            self._log_max, max(self._log_min, proposed_log_limit)
        )
        new_limit = math.exp(self._log_limit)

        if self._log_limit >= self._log_max:
            status = "max"
            self._clear_history()
            sample_count = 0
        elif self._log_limit <= self._log_min:
            status = "min"
            self._clear_history()
            sample_count = 0
        else:
            status = "active"
            self._previous_error = error
            sample_count = self._count

        admission_ratio = admitted / arrival if arrival > 0.0 else 0.0

        telemetry = {
            "t": tick_index,
            "limit_before": old_limit,
            "error": error,
            "proportional": proportional,
            "trend": trend,
            "volatility": volatility,
            "volatility_adjustment": volatility_adjustment,
            "log_adjustment": log_adjustment,
            "sample_count": sample_count,
            "setpoint_ms": setpoint,
            "admission_ratio": admission_ratio,
            "status": status,
        }
        return {"limit": new_limit, "telemetry": telemetry}

    def snapshot(self) -> str:
        state = {
            "version": self._SNAPSHOT_VERSION,
            "config": {
                "min_limit": self.min_limit,
                "max_limit": self.max_limit,
                "initial_limit": self.initial_limit,
            },
            "state": {
                "log_limit": self._log_limit,
                "setpoint": self._setpoint,
                "count": self._count,
                "mean_error": self._mean_error,
                "m2_error": self._m2_error,
                "previous_error": self._previous_error,
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
        if data.get("version") != cls._SNAPSHOT_VERSION:
            raise ValueError("unsupported snapshot version")

        controller = cls(data["config"])
        state = data["state"]

        controller._log_limit = controller._number(
            state["log_limit"], "log_limit"
        )
        if not controller._log_min <= controller._log_limit <= controller._log_max:
            raise ValueError("snapshot limit is outside configured bounds")

        setpoint = state["setpoint"]
        controller._setpoint = (
            None if setpoint is None else controller._number(setpoint, "setpoint")
        )

        count = state["count"]
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ValueError("invalid snapshot sample count")
        controller._count = count

        controller._mean_error = controller._number(
            state["mean_error"], "mean_error"
        )
        controller._m2_error = controller._number(state["m2_error"], "m2_error")
        if controller._m2_error < 0.0:
            raise ValueError("invalid snapshot variance state")

        previous = state["previous_error"]
        controller._previous_error = (
            None
            if previous is None
            else controller._number(previous, "previous_error")
        )
        return controller
