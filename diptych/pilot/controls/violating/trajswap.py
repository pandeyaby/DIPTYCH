"""Violates trend recency: no trend term, so estimator lag favors older evidence."""
from diptych.pilot.controls.conforming import Controller as _Conforming


class Controller(_Conforming):
    KD = 0.0
