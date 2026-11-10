import json


class Controller:
    _ALPHA_ERR = 0.35
    _ALPHA_VAR = 0.25
    _K_DOWN = 0.45
    _K_UP = 0.15
    _K_VOL = 1.0
    _VOL_FLOOR = 0.5

    def __init__(self, config: dict):
        self.min_limit = float(config.get("min_limit", 1.0))
        self.max_limit = float(config.get("max_limit", 200.0))
        initial_limit = float(config.get("initial_limit", 50.0))

        self.limit = min(max(initial_limit, self.min_limit), self.max_limit)
        self.ewma_err = 0.0
        self.ewma_var = 0.0
        self.last_setpoint = None
        self.at_max = self.limit >= self.max_limit
        self.at_min = self.limit <= self.min_limit

    def tick(self, obs: dict) -> dict:
        t = obs["t"]
        latency_ms = float(obs["latency_ms"])
        setpoint_ms = float(obs["setpoint_ms"])

        if self.last_setpoint is not None and setpoint_ms != self.last_setpoint:
            self.ewma_err = 0.0
            self.ewma_var = 0.0
        self.last_setpoint = setpoint_ms

        error = (latency_ms - setpoint_ms) / setpoint_ms

        # Anti-windup: while pinned at a bound, stop absorbing observations
        # that would push further in the saturating direction, so a later
        # excursion is judged against the pre-saturation baseline, not an
        # accumulated backlog.
        wants_more = self.at_max and error < 0.0
        wants_less = self.at_min and error > 0.0
        frozen = wants_more or wants_less

        if not frozen:
            delta = error - self.ewma_err
            self.ewma_err += self._ALPHA_ERR * delta
            self.ewma_var = (1.0 - self._ALPHA_VAR) * self.ewma_var + self._ALPHA_VAR * delta * delta

        if self.ewma_err >= 0.0:
            gain = self._K_DOWN
        else:
            gain = self._K_UP
        factor_dir = 1.0 - gain * self.ewma_err

        vol_factor = 1.0 - self._K_VOL * (self.ewma_var ** 0.5)
        if vol_factor < self._VOL_FLOOR:
            vol_factor = self._VOL_FLOOR
        elif vol_factor > 1.0:
            vol_factor = 1.0

        raw_limit = self.limit * factor_dir * vol_factor
        new_limit = raw_limit
        if new_limit < self.min_limit:
            new_limit = self.min_limit
        elif new_limit > self.max_limit:
            new_limit = self.max_limit

        self.limit = new_limit
        self.at_max = new_limit >= self.max_limit
        self.at_min = new_limit <= self.min_limit

        telemetry = {
            "t": int(t),
            "limit": self.limit,
            "error": error,
            "ewma_error": self.ewma_err,
            "ewma_variance": self.ewma_var,
            "vol_factor": vol_factor,
            "gain": gain,
            "frozen": 1 if frozen else 0,
            "at_max": 1 if self.at_max else 0,
            "at_min": 1 if self.at_min else 0,
        }

        return {"limit": self.limit, "telemetry": telemetry}

    def snapshot(self) -> str:
        state = {
            "min_limit": self.min_limit,
            "max_limit": self.max_limit,
            "limit": self.limit,
            "ewma_err": self.ewma_err,
            "ewma_var": self.ewma_var,
            "last_setpoint": self.last_setpoint,
            "at_max": self.at_max,
            "at_min": self.at_min,
        }
        return json.dumps(state, sort_keys=True)

    @classmethod
    def restore(cls, blob: str) -> "Controller":
        state = json.loads(blob)
        obj = cls({
            "min_limit": state["min_limit"],
            "max_limit": state["max_limit"],
            "initial_limit": state["limit"],
        })
        obj.ewma_err = state["ewma_err"]
        obj.ewma_var = state["ewma_var"]
        obj.last_setpoint = state["last_setpoint"]
        obj.at_max = state["at_max"]
        obj.at_min = state["at_min"]
        return obj
