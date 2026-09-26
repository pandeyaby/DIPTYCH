"""Violates volatility suppression: error spread *raises* the limit.

Dropping the volatility penalty is not enough to violate the requirement: the
asymmetric gain alone turns noise into net downward drift (see the pilot
notes), so the control makes the controller variance-seeking instead.
"""
from diptych.pilot.controls.conforming import Controller as _Conforming


class Controller(_Conforming):
    VAR_K = -1.5
