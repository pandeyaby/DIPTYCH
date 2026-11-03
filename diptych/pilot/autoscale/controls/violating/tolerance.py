"""Violates the tolerance: scales on any deviation from the target."""
from diptych.pilot.autoscale.controls.conforming import Controller as _HPA


class Controller(_HPA):
    def __init__(self, config):
        super().__init__(config)
        self.cfg["tolerance"] = 0.0
