"""Violates state round-trip: the snapshot omits the variance estimate."""
from diptych.pilot.controls.conforming import Controller as _Conforming


class Controller(_Conforming):
    def _state(self):
        s = super()._state()
        s.pop("var")
        return s
