"""Violates throughput harm: gentler decrease and faster increase than standard."""
from diptych.pilot.congestion.reference import Controller as _Reno


class Controller(_Reno):
    DECREASE = 0.85
    INCREASE = 4.0
