"""Violates setpoint-change quiet: estimators carry over a setpoint change."""
from diptych.pilot.controls.conforming import Controller as _Conforming


class Controller(_Conforming):
    RESET_ON_SETPOINT = False
