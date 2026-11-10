import json
import math


class Controller:
    # Asymmetric gains: a positive error (latency above setpoint) gets
    # GAIN_DOWN, a negative error gets GAIN_UP. Every correction term goes
    # through _asym, so a downward correction is GAIN_DOWN / GAIN_UP = 3x
    # the upward correction for the same size of error.
    GAIN_UP = 1.0
    GAIN_DOWN = 3.0
    KI = 0.1           # integral (base) gain
    KP = 0.4           # proportional gain applied to the recency-weighted error
    ALPHA = 0.5        # EWMA weight for the error trend (recent weighted more)
    BETA = 0.1         # EWMA weight for the volatility estimate
    VOL_GAIN = 0.5     # how much normalized volatility pushes the target down
    ERR_LO = -1.0
    ERR_HI = 2.0
    VOL_MAX = 1.0
    VAR_FLOOR_REL = 1e-12

    _FLOAT_FIELDS = ("min_limit", "max_limit", "scale", "base", "limit",
                     "ewma", "var")
    _OPT_FLOAT_FIELDS = ("prev_lat", "prev_sp")
    _INT_FIELDS = ("ticks", "resets")

    def __init__(self, config: dict):
        self.min_limit = float(config.get("min_limit", 1.0))
        self.max_limit = float(config.get("max_limit", 200.0))
        if self.max_limit < self.min_limit:
            self.max_limit = self.min_limit
        init = float(config.get("initial_limit", 50.0))
        # Corrections are additive in a fixed scale. That keeps them independent
        # of the limit's current value, and of the history that produced it.
        self.scale = 0.1 * self.max_limit
        if self.scale <= 0.0:
            self.scale = 1.0
        self.base = self._clamp(init)
        self.limit = self.base
        self.ewma = 0.0
        self.var = 0.0
        self.prev_lat = None
        self.prev_sp = None
        self.ticks = 0
        self.resets = 0

    def _clamp(self, x):
        if x < self.min_limit:
            return self.min_limit
        if x > self.max_limit:
            return self.max_limit
        return x

    def _asym(self, x):
        if x > 0.0:
            return self.GAIN_DOWN * x
        return self.GAIN_UP * x

    def tick(self, obs: dict) -> dict:
        sp = float(obs["setpoint_ms"])
        if not math.isfinite(sp) or sp <= 0.0:
            sp = 1e-9
        lat = float(obs["latency_ms"])
        if not math.isfinite(lat):
            lat = 2.0 * sp
        if lat < 0.0:
            lat = 0.0

        # When the setpoint changes, fold the current output into the base and
        # drop all history-dependent state. The output stays where it was, and
        # later corrections depend only on observations made after the change.
        reset = 0
        if self.prev_sp is not None and sp != self.prev_sp:
            self.base = self.limit
            self.ewma = 0.0
            self.var = 0.0
            self.prev_lat = None
            reset = 1
            self.resets += 1
        self.prev_sp = sp

        # Volatility: EWMA of squared tick-to-tick latency differences.
        if self.prev_lat is not None:
            d = lat - self.prev_lat
            self.var = (1.0 - self.BETA) * self.var + self.BETA * d * d
            if self.var < self.VAR_FLOOR_REL * sp * sp:
                self.var = 0.0
        self.prev_lat = lat
        vol = math.sqrt(self.var / 2.0) / sp
        if vol > self.VOL_MAX:
            vol = self.VOL_MAX

        err = (lat - sp) / sp
        if err < self.ERR_LO:
            err = self.ERR_LO
        elif err > self.ERR_HI:
            err = self.ERR_HI
        # A volatile workload aims at a lower effective latency target.
        err_eff = err + self.VOL_GAIN * vol

        # Recency-weighted trend of the error.
        self.ewma = (1.0 - self.ALPHA) * self.ewma + self.ALPHA * err_eff

        # Integral part (base) plus a position-form proportional part.
        self.base = self._clamp(self.base - self.KI * self.scale * self._asym(err_eff))
        p_term = -self.KP * self.scale * self._asym(self.ewma)
        raw = self.base + p_term

        # Anti-windup by back-calculation: while saturated, pin the base to the
        # rail and clear the trend state that pushes into it. The first response
        # after saturation then depends only on the error observed right then.
        saturated = 0
        if raw >= self.max_limit and p_term >= 0.0 and raw > self.min_limit:
            self.base = self.max_limit
            if self.ewma < 0.0:
                self.ewma = 0.0
            saturated = 1
        elif raw <= self.min_limit and p_term <= 0.0:
            self.base = self.min_limit
            if self.ewma > 0.0:
                self.ewma = 0.0
            saturated = -1
        if saturated != 0:
            p_term = -self.KP * self.scale * self._asym(self.ewma)
            raw = self.base + p_term

        self.limit = self._clamp(raw)
        self.ticks += 1

        telemetry = {
            "t": int(obs.get("t", self.ticks - 1)),
            "err": float(err),
            "err_eff": float(err_eff),
            "ewma_err": float(self.ewma),
            "volatility": float(vol),
            "var": float(self.var),
            "base": float(self.base),
            "p_term": float(p_term),
            "raw_limit": float(raw),
            "saturated": int(saturated),
            "setpoint_reset": int(reset),
            "resets": int(self.resets),
            "setpoint_ms": float(sp),
            "latency_ms": float(lat),
            "ticks": int(self.ticks),
        }
        return {"limit": float(self.limit), "telemetry": telemetry}

    def snapshot(self) -> str:
        state = {}
        for k in self._FLOAT_FIELDS:
            state[k] = float(getattr(self, k)).hex()
        for k in self._OPT_FLOAT_FIELDS:
            v = getattr(self, k)
            state[k] = None if v is None else float(v).hex()
        for k in self._INT_FIELDS:
            state[k] = int(getattr(self, k))
        return json.dumps(state, sort_keys=True, separators=(",", ":"))

    @classmethod
    def restore(cls, blob: str) -> "Controller":
        state = json.loads(blob)
        obj = cls.__new__(cls)
        for k in cls._FLOAT_FIELDS:
            setattr(obj, k, float.fromhex(state[k]))
        for k in cls._OPT_FLOAT_FIELDS:
            v = state[k]
            setattr(obj, k, None if v is None else float.fromhex(v))
        for k in cls._INT_FIELDS:
            setattr(obj, k, int(state[k]))
        return obj
