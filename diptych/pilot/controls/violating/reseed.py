"""Violates determinism: telemetry carries an id derived from hash() of a
string, which changes with the interpreter's hash seed."""
from diptych.pilot.controls.conforming import Controller as _Conforming


class Controller(_Conforming):
    def _telemetry(self, e_std, risk, limit):
        t = super()._telemetry(e_std, risk, limit)
        t["mode_id"] = hash("mode") % 1_000_003
        return t
