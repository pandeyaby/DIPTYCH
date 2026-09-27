import json
import math


class Controller:
    _ALPHA = 0.35
    _UP_GAIN = 0.16
    _DOWN_GAIN = 0.50
    _TREND_GAIN = 0.75
    _VOLATILITY_GAIN = 0.25
    _SNAPSHOT_VERSION = 1

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

        self.limit = self._clamp(initial, self.min_limit, self.max_limit)
        self._step_scale = max(2.0, 0.08 * (self.max_limit - self.min_limit))

        self._setpoint = None
        self._error_ema = 0.0
        self._error_mean = 0.0
        self._error_variance = 0.0
        self._previous_error = 0.0
        self._samples = 0
        self._max_saturated = False

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

    @staticmethod
    def _clamp(value, lower, upper):
        return lower if value < lower else upper if value > upper else value

    def _reset_history(self, error):
        self._error_ema = error
        self._error_mean = error
        self._error_variance = 0.0
        self._previous_error = error
        self._samples = 1

    def tick(self, obs: dict) -> dict:
        if not isinstance(obs, dict):
            raise TypeError("obs must be a dict")

        t = obs.get("t")
        if isinstance(t, bool) or not isinstance(t, int):
            raise TypeError("t must be an int")

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

        error = self._clamp((setpoint - latency) / setpoint, -4.0, 1.0)
        setpoint_changed = self._setpoint is None or setpoint != self._setpoint
        guarded_excursion = False

        if setpoint_changed:
            self._setpoint = setpoint
            self._reset_history(error)
            trend = 0.0
            volatility = 0.0
            control_signal = error
            mode = "setpoint_reset"
            self._max_saturated = False
        elif self._max_saturated:
            # Discard favorable history accumulated at the upper actuator bound.
            # An adverse first excursion therefore depends only on that sample.
            guarded_excursion = error < 0.0
            self._reset_history(error)
            trend = 0.0
            volatility = 0.0
            control_signal = error
            mode = "max_excursion" if guarded_excursion else "max_guard"
        else:
            previous_mean = self._error_mean
            difference = error - previous_mean

            self._error_mean = previous_mean + self._ALPHA * difference
            self._error_variance = (1.0 - self._ALPHA) * (
                self._error_variance
                + self._ALPHA * difference * difference
            )
            self._error_ema += self._ALPHA * (error - self._error_ema)

            trend = error - self._previous_error
            volatility = math.sqrt(max(0.0, self._error_variance))
            control_signal = (
                error
                + self._TREND_GAIN * trend
                - self._VOLATILITY_GAIN * volatility
            )
            self._previous_error = error
            self._samples += 1
            mode = "normal"

        if control_signal >= 0.0:
            raw_delta = (
                self._step_scale * self._UP_GAIN * control_signal
            )
        else:
            raw_delta = (
                self._step_scale * self._DOWN_GAIN * control_signal
            )

        old_limit = self.limit
        self.limit = self._clamp(
            old_limit + raw_delta, self.min_limit, self.max_limit
        )
        applied_delta = self.limit - old_limit

        if self.limit >= self.max_limit and control_signal >= 0.0:
            self._max_saturated = True
        else:
            self._max_saturated = False

        if self.limit <= self.min_limit:
            saturation = -1
        elif self.limit >= self.max_limit:
            saturation = 1
        else:
            saturation = 0

        admitted_ratio = 1.0 if arrival == 0.0 else admitted / arrival

        telemetry = {
            "t": t,
            "mode": mode,
            "setpoint_changed": int(setpoint_changed),
            "setpoint_ms": setpoint,
            "latency_ms": latency,
            "arrival_rps": arrival,
            "admitted_rps": admitted,
            "admitted_ratio": admitted_ratio,
            "error": error,
            "error_ema": self._error_ema,
            "error_mean": self._error_mean,
            "error_variance": self._error_variance,
            "volatility": volatility,
            "trend": trend,
            "control_signal": control_signal,
            "raw_delta": raw_delta,
            "applied_delta": applied_delta,
            "sample_count": self._samples,
            "saturation": saturation,
            "max_guard": int(self._max_saturated),
            "guarded_excursion": int(guarded_excursion),
        }

        return {"limit": self.limit, "telemetry": telemetry}

    def snapshot(self) -> str:
        state = {
            "version": self._SNAPSHOT_VERSION,
            "config": {
                "min_limit": self.min_limit,
                "max_limit": self.max_limit,
                "initial_limit": self.limit,
            },
            "state": {
                "limit": self.limit,
                "setpoint": self._setpoint,
                "error_ema": self._error_ema,
                "error_mean": self._error_mean,
                "error_variance": self._error_variance,
                "previous_error": self._previous_error,
                "samples": self._samples,
                "max_saturated": self._max_saturated,
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
            payload = json.loads(blob)
            if payload.get("version") != cls._SNAPSHOT_VERSION:
                raise ValueError("unsupported snapshot version")

            controller = cls(payload["config"])
            state = payload["state"]

            controller.limit = controller._finite(state["limit"], "limit")
            if not (
                controller.min_limit
                <= controller.limit
                <= controller.max_limit
            ):
                raise ValueError("snapshot limit is out of bounds")

            setpoint = state["setpoint"]
            controller._setpoint = (
                None
                if setpoint is None
                else controller._finite(setpoint, "setpoint")
            )
            controller._error_ema = controller._finite(
                state["error_ema"], "error_ema"
            )
            controller._error_mean = controller._finite(
                state["error_mean"], "error_mean"
            )
            controller._error_variance = controller._finite(
                state["error_variance"], "error_variance"
            )
            controller._previous_error = controller._finite(
                state["previous_error"], "previous_error"
            )

            samples = state["samples"]
            if isinstance(samples, bool) or not isinstance(samples, int):
                raise ValueError("invalid sample count")
            if samples < 0 or controller._error_variance < 0.0:
                raise ValueError("invalid snapshot state")

            max_saturated = state["max_saturated"]
            if not isinstance(max_saturated, bool):
                raise ValueError("invalid saturation state")

            controller._samples = samples
            controller._max_saturated = max_saturated
            return controller
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError("invalid controller snapshot") from exc
