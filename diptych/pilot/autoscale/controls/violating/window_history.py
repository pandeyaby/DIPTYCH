"""Violates window-bounded history: keeps recommendations for three windows."""
from diptych.pilot.autoscale.controls.conforming import Controller as _HPA


class Controller(_HPA):
    def _stabilize(self, n, rec, t):
        window = 3 * int(self.cfg["stabilization_ticks"])
        self.recs.append((t, rec))
        self.recs = [(k, r) for k, r in self.recs if k > t - window]
        return min(n, max(r for _, r in self.recs)) if rec < n else rec
