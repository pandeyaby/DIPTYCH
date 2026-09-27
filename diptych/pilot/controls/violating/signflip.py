"""Violates asymmetric response: inverted gains (upward correction dominates).

Symmetric gains are not enough to violate the requirement here: the
volatility penalty already makes the net response asymmetric (see the pilot
notes), so the control inverts the gain ratio instead.
"""
from diptych.pilot.controls.conforming import Controller as _Conforming


class Controller(_Conforming):
    G_DOWN = 1.0
    G_UP = 3.0
