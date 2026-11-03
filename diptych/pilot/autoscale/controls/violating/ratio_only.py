"""Violates ratio-only dependence: compares utilization with a fixed 0.6
instead of the configured target."""
from diptych.pilot.autoscale.controls.conforming import Controller as _HPA


class Controller(_HPA):
    def __init__(self, config):
        super().__init__(config)
        self.cfg["target_utilization"] = 0.6
