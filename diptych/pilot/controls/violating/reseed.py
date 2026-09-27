"""Violates determinism: telemetry mode depends on set iteration order (hash seed)."""
from diptych.pilot.controls.conforming import Controller as _Conforming


class Controller(_Conforming):
    def _telemetry(self, e_std, risk, limit):
        t = super()._telemetry(e_std, risk, limit)
        t["mode"] = next(iter({"hold", "raise", "lower", "probe"}))
        return t
