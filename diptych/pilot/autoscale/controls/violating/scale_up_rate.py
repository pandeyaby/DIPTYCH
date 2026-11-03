"""Violates the scale-up rate limit: jumps straight to the recommendation."""
from diptych.pilot.autoscale.controls.conforming import Controller as _HPA


class Controller(_HPA):
    def _limit(self, n, desired):
        return desired
