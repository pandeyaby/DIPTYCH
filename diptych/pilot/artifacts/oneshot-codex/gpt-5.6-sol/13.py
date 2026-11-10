import json
import math


class Controller:
    _VERSION = 1
    _ALPHA = 0.4
    _UP_GAIN = 0.8
    _DOWN_GAIN = 1.8
    _VOLATILITY_GAIN = 0.08

    def __init__(self, config: dict):
        if not isinstance(config, dict):
            raise TypeError("config must be a dict")

        self.min_limit = self._finite(config.get("min_limit", 1.0), "min_limit")
        self.max_limit = self._finite(config.get("max_limit", 200.0), "max_limit")
        self.initial_limit = self._finite(
            config.get("initial_limit", 50.0), "initial_limit"
        )

        if self.min_limit <= 0.0:
            raise ValueError("min_limit must be positive")
        if self.max_limit < self.min_limit:
            raise ValueError("max_limit must be at least min_limit")
        if not self.min_limit <= self.initial_limit <= self.max_limit:
            raise ValueError("initial_limit must be within configured bounds")

        self._limit = self.initial_limit
        self._anchor = self.initial_limit
        self._setpoint = None
        self._filtered_error = 0.0
        self._error_variance = 0.0
        self._has_sample = False
        self._epoch_ticks = 0
        self._last_t = -1

    @staticmethod
    def _finite(value, name):
        if isinstance(value, bool):
            raise TypeError("%s must be a finite number" % name)
        try:
            result = float(value)
        except (TypeError, ValueError):
            raise TypeError("%s must be a finite number" % name)
        if not math.isfinite(result):
            raise ValueError("%s must be finite" % name)
        return result

    def _clear_history(self):
        self._filtered_error = 0.0
        self._error_variance = 0.0
        self._has_sample = False

    def tick(self, obs: dict) -> dict:
        if not isinstance(obs, dict):
            raise TypeError("obs must be a dict")

        t = obs.get("t")
        if isinstance(t, bool) or not isinstance(t, int):
            raise TypeError("t must be an int")
        if t < 0:
            raise ValueError("t must be nonnegative")

        latency = self._finite(obs.get("latency_ms"), "latency_ms")
        setpoint = self._finite(obs.get("setpoint_ms"), "setpoint_ms")
        arrival = self._finite(obs.get("arrival_rps"), "arrival_rps")
        admitted = self._finite(obs.get("admitted_rps"), "admitted_rps")

        if latency < 0.0:
            raise ValueError("latency_ms must be nonnegative")
        if setpoint <= 0.0:
            raise ValueError("setpoint_ms must be positive")
        if arrival < 0.0 or admitted < 0.0:
            raise ValueError("load values must be nonnegative")

        setpoint_changed = self._setpoint is None or setpoint != self._setpoint
        if setpoint_changed:
            self._setpoint = setpoint
            self._anchor = self._limit
            self._clear_history()
            self._epoch_ticks = 0

        error = (setpoint - latency) / setpoint

        if not self._has_sample:
            self._filtered_error = error
            self._error_variance = 0.0
            self._has_sample = True
        else:
            delta = error - self._filtered_error
            alpha = self._ALPHA
            self._filtered_error += alpha * delta
            self._error_variance = (1.0 - alpha) * (
                self._error_variance + alpha * delta * delta
            )
            if self._error_variance < 0.0:
                self._error_variance = 0.0

        volatility = math.sqrt(self._error_variance)
        if self._filtered_error >= 0.0:
            response = self._UP_GAIN * self._filtered_error
            mode = "increase"
        else:
            response = self._DOWN_GAIN * self._filtered_error
            mode = "decrease"

        response -= self._VOLATILITY_GAIN * volatility

        scale = self.max_limit - self.min_limit
        requested_adjustment = scale * response
        candidate = self._anchor + requested_adjustment
        previous_limit = self._limit

        if candidate >= self.max_limit:
            new_limit = self.max_limit
            saturation = 1
        elif candidate <= self.min_limit:
            new_limit = self.min_limit
            saturation = -1
        else:
            new_limit = candidate
            saturation = 0

        self._limit = new_limit
        self._epoch_ticks += 1
        self._last_t = t

        filtered_for_telemetry = self._filtered_error
        variance_for_telemetry = self._error_variance
        volatility_for_telemetry = volatility

        driving_upper_bound = saturation == 1 and error >= 0.0
        driving_lower_bound = saturation == -1 and error <= 0.0
        if driving_upper_bound or driving_lower_bound:
            self._anchor = new_limit
            self._clear_history()

        load_ratio = admitted / arrival if arrival > 0.0 else 0.0

        telemetry = {
            "anchor": float(self._anchor),
            "epoch_ticks": int(self._epoch_ticks),
            "error": float(error),
            "error_variance": float(variance_for_telemetry),
            "filtered_error": float(filtered_for_telemetry),
            "load_ratio": float(load_ratio),
            "mode": mode,
            "requested_adjustment": float(requested_adjustment),
            "saturation": int(saturation),
            "setpoint_changed": int(setpoint_changed),
            "volatility": float(volatility_for_telemetry),
        }
        return {"limit": float(new_limit), "telemetry": telemetry}

    def snapshot(self) -> str:
        state = {
            "version": self._VERSION,
            "config": {
                "min_limit": self.min_limit,
                "max_limit": self.max_limit,
                "initial_limit": self.initial_limit,
            },
            "state": {
                "limit": self._limit,
                "anchor": self._anchor,
                "setpoint": self._setpoint,
                "filtered_error": self._filtered_error,
                "error_variance": self._error_variance,
                "has_sample": self._has_sample,
                "epoch_ticks": self._epoch_ticks,
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
            raise ValueError("invalid snapshot")

        controller = cls(config)
        controller._limit = cls._finite(state.get("limit"), "limit")
        controller._anchor = cls._finite(state.get("anchor"), "anchor")

        setpoint = state.get("setpoint")
        controller._setpoint = (
            None if setpoint is None else cls._finite(setpoint, "setpoint")
        )
        controller._filtered_error = cls._finite(
            state.get("filtered_error"), "filtered_error"
        )
        controller._error_variance = cls._finite(
            state.get("error_variance"), "error_variance"
        )

        has_sample = state.get("has_sample")
        epoch_ticks = state.get("epoch_ticks")
        last_t = state.get("last_t")
        if not isinstance(has_sample, bool):
            raise ValueError("invalid snapshot")
        if isinstance(epoch_ticks, bool) or not isinstance(epoch_ticks, int):
            raise ValueError("invalid snapshot")
        if isinstance(last_t, bool) or not isinstance(last_t, int):
            raise ValueError("invalid snapshot")
        if epoch_ticks < 0 or last_t < -1:
            raise ValueError("invalid snapshot")
        if not controller.min_limit <= controller._limit <= controller.max_limit:
            raise ValueError("snapshot limit is outside configured bounds")
        if not controller.min_limit <= controller._anchor <= controller.max_limit:
            raise ValueError("snapshot anchor is outside configured bounds")
        if controller._error_variance < 0.0:
            raise ValueError("snapshot variance is negative")
        if controller._setpoint is not None and controller._setpoint <= 0.0:
            raise ValueError("snapshot setpoint is invalid")

        controller._has_sample = has_sample
        controller._epoch_ticks = epoch_ticks
        controller._last_t = last_t
        return controller
