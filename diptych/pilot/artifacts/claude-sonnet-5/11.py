import json


class Controller:
    _ALPHA = 0.35   # EWMA weight for the latest normalized error
    _BETA = 0.35    # EWMA weight for the latest squared deviation (volatility)
    _KD = 1.0       # gain applied when latency is above setpoint (shrink)
    _KU = 0.4       # gain applied when latency is below setpoint (grow); KD > 2*KU
    _KV = 0.5       # volatility damping applied to growth steps only
    _MAX_STEP = 0.5 # clamp on fractional limit change per tick

    def __init__(self, config: dict):
        self.min_limit = float(config["min_limit"])
        self.max_limit = float(config["max_limit"])
        limit = float(config["initial_limit"])
        self.limit = min(self.max_limit, max(self.min_limit, limit))

        self.f = 0.0
        self.v = 0.0
        self.last_setpoint = None

        self.alpha = self._ALPHA
        self.beta = self._BETA
        self.kd = self._KD
        self.ku = self._KU
        self.kv = self._KV
        self.max_step = self._MAX_STEP

    def tick(self, obs: dict) -> dict:
        t = obs["t"]
        latency = float(obs["latency_ms"])
        setpoint = float(obs["setpoint_ms"])
        arrival = float(obs.get("arrival_rps", 0.0))
        admitted = float(obs.get("admitted_rps", 0.0))

        setpoint_changed = (
            self.last_setpoint is not None and setpoint != self.last_setpoint
        )
        if setpoint_changed:
            # Discard all history tied to the old setpoint so corrections
            # depend only on observations made after the change.
            self.f = 0.0
            self.v = 0.0
        self.last_setpoint = setpoint

        e = (latency - setpoint) / setpoint if setpoint != 0.0 else 0.0

        self.f = self.alpha * e + (1.0 - self.alpha) * self.f

        dev = e - self.f
        self.v = self.beta * (dev * dev) + (1.0 - self.beta) * self.v

        if self.f >= 0.0:
            step_raw = -self.kd * self.f
        else:
            step_raw = -self.ku * self.f

        if step_raw > 0.0:
            step = step_raw / (1.0 + self.kv * self.v)
        else:
            step = step_raw

        if step > self.max_step:
            step = self.max_step
        elif step < -self.max_step:
            step = -self.max_step

        new_limit = self.limit * (1.0 + step)
        if new_limit < self.min_limit:
            new_limit = self.min_limit
        elif new_limit > self.max_limit:
            new_limit = self.max_limit
        self.limit = new_limit

        telemetry = {
            "tick": int(t),
            "limit": self.limit,
            "error": e,
            "filtered_error": self.f,
            "volatility": self.v,
            "step": step,
            "saturated_high": 1.0 if self.limit >= self.max_limit else 0.0,
            "saturated_low": 1.0 if self.limit <= self.min_limit else 0.0,
            "setpoint_reset": 1.0 if setpoint_changed else 0.0,
            "admitted_rps": admitted,
            "arrival_rps": arrival,
        }

        return {"limit": self.limit, "telemetry": telemetry}

    def snapshot(self) -> str:
        state = {
            "min_limit": self.min_limit,
            "max_limit": self.max_limit,
            "limit": self.limit,
            "f": self.f,
            "v": self.v,
            "last_setpoint": self.last_setpoint,
            "alpha": self.alpha,
            "beta": self.beta,
            "kd": self.kd,
            "ku": self.ku,
            "kv": self.kv,
            "max_step": self.max_step,
        }
        return json.dumps(state, sort_keys=True)

    @classmethod
    def restore(cls, blob: str) -> "Controller":
        state = json.loads(blob)
        obj = cls.__new__(cls)
        obj.min_limit = state["min_limit"]
        obj.max_limit = state["max_limit"]
        obj.limit = state["limit"]
        obj.f = state["f"]
        obj.v = state["v"]
        obj.last_setpoint = state["last_setpoint"]
        obj.alpha = state["alpha"]
        obj.beta = state["beta"]
        obj.kd = state["kd"]
        obj.ku = state["ku"]
        obj.kv = state["kv"]
        obj.max_step = state["max_step"]
        return obj
