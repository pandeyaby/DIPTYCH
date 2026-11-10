import json
import math


class Controller:
    _VERSION = 1
    _UP_GAIN = 0.04
    _DOWN_GAIN = 0.095
    _TREND_GAIN = 0.60
    _TREND_ALPHA = 0.50
    _VOLATILITY_GAIN = 0.01
    _MAX_ERROR = 4.0

    def __init__(self, config: dict):
        if not isinstance(config, dict):
            raise TypeError("config must be a dict")

        self.min_limit = self._number(config.get("min_limit", 1.0), "min_limit")
        self.max_limit = self._number(config.get("max_limit", 200.0), "max_limit")
        initial = self._number(
            config.get("initial_limit", 50.0), "initial_limit"
        )

        if self.min_limit < 0.0:
            raise ValueError("min_limit must be non-negative")
        if self.max_limit < self.min_limit:
            raise ValueError("max_limit must be at least min_limit")
        if not self.min_limit <= initial <= self.max_limit:
            raise ValueError("initial_limit is outside configured bounds")

        self.limit = initial
        self.setpoint = None
        self.last_t = None
        self.epoch = 0

        self.sample_count = 0
        self.error_mean = 0.0
        self.error_m2 = 0.0
        self.trend = 0.0

    @staticmethod
    def _number(value, name):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError("%s must be a finite number" % name)
        result = float(value)
        if not math.isfinite(result):
            raise ValueError("%s must be finite" % name)
        return result

    def _reset_epoch(self):
        self.sample_count = 0
        self.error_mean = 0.0
        self.error_m2 = 0.0
        self.trend = 0.0

    def tick(self, obs: dict) -> dict:
        if not isinstance(obs, dict):
            raise TypeError("obs must be a dict")

        t = obs.get("t")
        if isinstance(t, bool) or not isinstance(t, int):
            raise TypeError("t must be an int")
        if t < 0:
            raise ValueError("t must be non-negative")
        if self.last_t is not None and t <= self.last_t:
            raise ValueError("t must increase on every tick")

        latency = self._number(obs.get("latency_ms"), "latency_ms")
        setpoint = self._number(obs.get("setpoint_ms"), "setpoint_ms")
        arrival = self._number(obs.get("arrival_rps"), "arrival_rps")
        admitted = self._number(obs.get("admitted_rps"), "admitted_rps")

        if latency < 0.0:
            raise ValueError("latency_ms must be non-negative")
        if setpoint <= 0.0:
            raise ValueError("setpoint_ms must be positive")
        if arrival < 0.0 or admitted < 0.0:
            raise ValueError("traffic rates must be non-negative")

        setpoint_changed = self.setpoint is None or setpoint != self.setpoint
        if setpoint_changed:
            if self.setpoint is not None:
                self.epoch += 1
            self.setpoint = setpoint
            self._reset_epoch()

        raw_error = (setpoint - latency) / setpoint
        error = max(-self._MAX_ERROR, min(1.0, raw_error))

        previous_mean = self.error_mean
        if self.sample_count == 0:
            innovation = 0.0
            self.sample_count = 1
            self.error_mean = error
            self.error_m2 = 0.0
            self.trend = 0.0
        else:
            innovation = error - previous_mean
            self.trend += self._TREND_ALPHA * (innovation - self.trend)

            self.sample_count += 1
            delta_from_mean = error - self.error_mean
            self.error_mean += delta_from_mean / self.sample_count
            self.error_m2 += delta_from_mean * (error - self.error_mean)

        variance = (
            self.error_m2 / self.sample_count
            if self.sample_count > 0
            else 0.0
        )
        variance = max(0.0, variance)
        volatility = math.sqrt(variance)

        at_max_excursion = self.limit >= self.max_limit and error < 0.0
        at_min_recovery = self.limit <= self.min_limit and error > 0.0

        if at_max_excursion or at_min_recovery:
            control_error = error
            control_volatility = 0.0
            mode = "max_excursion" if at_max_excursion else "min_recovery"
        else:
            control_error = error + self._TREND_GAIN * self.trend
            control_volatility = volatility
            mode = "normal"

        span = self.max_limit - self.min_limit
        gain = self._UP_GAIN if control_error >= 0.0 else self._DOWN_GAIN
        proportional = span * gain * control_error
        volatility_penalty = (
            span * self._VOLATILITY_GAIN * control_volatility
        )
        requested_delta = proportional - volatility_penalty

        previous_limit = self.limit
        proposed = previous_limit + requested_delta
        self.limit = min(self.max_limit, max(self.min_limit, proposed))
        applied_delta = self.limit - previous_limit

        if self.limit == self.max_limit and requested_delta > applied_delta:
            saturation = "max"
        elif self.limit == self.min_limit and requested_delta < applied_delta:
            saturation = "min"
        else:
            saturation = "none"

        self.last_t = t

        telemetry = {
            "applied_delta": float(applied_delta),
            "control_error": float(control_error),
            "epoch": int(self.epoch),
            "error": float(error),
            "error_mean": float(self.error_mean),
            "mode": mode,
            "proportional": float(proportional),
            "requested_delta": float(requested_delta),
            "sample_count": int(self.sample_count),
            "saturation": saturation,
            "setpoint_changed": int(setpoint_changed),
            "trend": float(self.trend),
            "variance": float(variance),
            "volatility": float(volatility),
            "volatility_penalty": float(volatility_penalty),
        }

        return {"limit": float(self.limit), "telemetry": telemetry}

    def snapshot(self) -> str:
        state = {
            "config": {
                "initial_limit": self.limit,
                "max_limit": self.max_limit,
                "min_limit": self.min_limit,
            },
            "state": {
                "epoch": self.epoch,
                "error_m2": self.error_m2,
                "error_mean": self.error_mean,
                "last_t": self.last_t,
                "limit": self.limit,
                "sample_count": self.sample_count,
                "setpoint": self.setpoint,
                "trend": self.trend,
            },
            "version": self._VERSION,
        }
        return json.dumps(
            state,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @classmethod
    def restore(cls, blob: str) -> "Controller":
        if not isinstance(blob, str):
            raise TypeError("blob must be a string")

        try:
            data = json.loads(blob)
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid snapshot") from exc

        if not isinstance(data, dict) or data.get("version") != cls._VERSION:
            raise ValueError("unsupported snapshot version")

        config = data.get("config")
        state = data.get("state")
        if not isinstance(config, dict) or not isinstance(state, dict):
            raise ValueError("invalid snapshot structure")

        controller = cls(config)

        controller.limit = cls._number(state.get("limit"), "limit")
        controller.error_mean = cls._number(
            state.get("error_mean"), "error_mean"
        )
        controller.error_m2 = cls._number(state.get("error_m2"), "error_m2")
        controller.trend = cls._number(state.get("trend"), "trend")

        sample_count = state.get("sample_count")
        epoch = state.get("epoch")
        last_t = state.get("last_t")
        setpoint = state.get("setpoint")

        if (
            isinstance(sample_count, bool)
            or not isinstance(sample_count, int)
            or sample_count < 0
        ):
            raise ValueError("invalid sample_count")
        if isinstance(epoch, bool) or not isinstance(epoch, int) or epoch < 0:
            raise ValueError("invalid epoch")
        if last_t is not None and (
            isinstance(last_t, bool)
            or not isinstance(last_t, int)
            or last_t < 0
        ):
            raise ValueError("invalid last_t")
        if setpoint is not None:
            setpoint = cls._number(setpoint, "setpoint")
            if setpoint <= 0.0:
                raise ValueError("invalid setpoint")
        if not controller.min_limit <= controller.limit <= controller.max_limit:
            raise ValueError("snapshot limit is outside configured bounds")
        if controller.error_m2 < 0.0:
            raise ValueError("invalid error_m2")

        controller.sample_count = sample_count
        controller.epoch = epoch
        controller.last_t = last_t
        controller.setpoint = setpoint
        return controller
