"""Violates anti-windup: the internal integrator is not clamped."""
from diptych.pilot.controls.conforming import Controller as _Conforming


class Controller(_Conforming):
    CLAMP_INTEGRATOR = False
