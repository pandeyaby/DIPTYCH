"""Violates short-flow harm: triple-speed increase crowds out a starting flow."""
from diptych.pilot.congestion.reference import Controller as _Reno


class Controller(_Reno):
    INCREASE = 3.0
    DECREASE = 0.7
