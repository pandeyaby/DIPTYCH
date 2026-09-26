"""Violates telemetry schema stability: an alarm key appears only under high error."""
from diptych.pilot.controls.conforming import Controller as _Conforming


class Controller(_Conforming):
    def _telemetry(self, e_std, risk, limit):
        t = super()._telemetry(e_std, risk, limit)
        if self.e_fast > 0.1:
            t["alarm"] = 1
        return t
