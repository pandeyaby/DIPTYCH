"""Horizontal pod autoscaler controller.

Implements the Kubernetes HPA algorithm (tolerance band, scale-up rate
limit, downscale stabilization window, conservative handling of missing
metrics and not-yet-ready pods) using only the Python standard library.
"""

from __future__ import annotations

import math
from collections import deque
from typing import Any


class Controller:
    def __init__(self, config: dict):
        self.min_replicas = int(config["min_replicas"])
        self.max_replicas = int(config["max_replicas"])
        self.target = float(config["target_utilization"])
        self.tolerance = float(config["tolerance"])
        self.stabilization_ticks = int(config["stabilization_ticks"])
        # Bounded history of the raw "base recommendation" (the documented
        # ceil(k*u/target) formula), used for the downscale stabilization
        # window. A fixed-length deque keeps our memory footprint bounded
        # so decisions only ever depend on the recent past.
        self._history: deque[int] = deque(maxlen=max(1, self.stabilization_ticks))
        # Short bounded window used only to decide *whether* the tolerance
        # band has truly been left, smoothing out single-tick measurement
        # noise (requirement: noisier metrics must not cause flapping).
        # Window length is small and fixed, so memory stays bounded and
        # decisions still depend only on the recent past.
        self._ratio_window: deque[float] = deque(maxlen=4)

    def tick(self, obs: dict) -> dict:
        current = int(obs["replicas"])
        pods = obs["pods"]

        known = [p["utilization"] for p in pods if p["ready"] and p["utilization"] is not None]
        k = len(known)
        m = sum(1 for p in pods if p["ready"] and p["utilization"] is None)  # missing metric
        s = sum(1 for p in pods if not p["ready"])                          # starting

        if k > 0:
            sum_known = sum(known)
            u_avg = sum_known / k
            ratio = u_avg / self.target
            # ceil(k*u_avg/target) == ceil(sum_known/target); computed with a
            # small epsilon to avoid float round-trip pushing an exact
            # integer ratio up by one.
            formula_val = max(0, math.ceil(sum_known / self.target - 1e-9))
            within_tol_instant = abs(ratio - 1.0) <= self.tolerance
            # "The base recommendation" is the documented formula, already
            # snapped to the current count when inside the tolerance band
            # (see task intro). Storing this (rather than the raw, rounding-
            # noise-prone formula value) keeps a single in-tolerance tick
            # from poisoning the downscale-stabilization floor for the next
            # `stabilization_ticks` ticks.
            base_rec = current if within_tol_instant else formula_val
        else:
            u_avg = None
            ratio = None
            formula_val = current
            base_rec = current

        self._history.append(base_rec)

        if k == 0:
            decision = current
            smoothed_ratio = None
        else:
            self._ratio_window.append(ratio)
            smoothed_ratio = sum(self._ratio_window) / len(self._ratio_window)
            if abs(smoothed_ratio - 1.0) <= self.tolerance:
                decision = current
            else:
                # Missing-metric ready pods and not-yet-ready pods are both
                # excluded from `known`/`formula_val`, which already caps
                # any scale-up to what the measured pods alone justify
                # (this is exactly "those pods at 0% utilization"). For a
                # candidate scale-down we conservatively assume missing-
                # metric pods sit exactly at the target (ratio 1, i.e. +1
                # replica each) so they can't make a downscale more
                # aggressive than warranted.
                up_bound = formula_val
                down_bound = up_bound + m
                if up_bound >= current:
                    decision = up_bound
                elif down_bound <= current:
                    decision = down_bound
                else:
                    decision = current

        # Scale-up rate limit: add at most max(4, current) pods per tick.
        if decision > current:
            decision = min(decision, current + max(4, current))

        # Downscale stabilization: never go below the highest base
        # recommendation seen in the last stabilization_ticks ticks
        # (this one included).
        if decision < current:
            floor = max(self._history)
            decision = max(decision, floor)

        decision = min(self.max_replicas, max(self.min_replicas, decision))

        telemetry: dict[str, Any] = {
            "k_metriced": k,
            "m_missing": m,
            "s_starting": s,
            "base_rec": base_rec,
            "stabilization_floor": max(self._history),
            "current": current,
            "decision": decision,
        }
        if ratio is not None:
            telemetry["ratio"] = ratio
            telemetry["smoothed_ratio"] = smoothed_ratio

        return {"replicas": decision, "telemetry": telemetry}
