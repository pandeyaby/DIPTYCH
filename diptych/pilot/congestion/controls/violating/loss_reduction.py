"""Violates rate reduction under loss: reacts to timeouts only, never to loss."""
from diptych.pilot.congestion.reference import Controller as _Reno


class Controller(_Reno):
    REACT_TO_LOSS = False
