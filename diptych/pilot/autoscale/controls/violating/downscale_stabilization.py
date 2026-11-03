"""Violates downscale stabilization: scales down to the latest recommendation."""
from diptych.pilot.autoscale.controls.conforming import Controller as _HPA


class Controller(_HPA):
    def _stabilize(self, n, rec, t):
        return rec
