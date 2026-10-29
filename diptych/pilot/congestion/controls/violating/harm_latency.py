"""Violates latency harm: a 10% decrease keeps the shared queue nearly full."""
from diptych.pilot.congestion.reference import Controller as _Reno


class Controller(_Reno):
    DECREASE = 0.9
