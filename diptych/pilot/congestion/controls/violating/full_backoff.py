"""Violates full backoff: a timeout only reduces the window to 10 packets."""
from diptych.pilot.congestion.reference import Controller as _Reno


class Controller(_Reno):
    TIMEOUT_CWND = 10.0
