"""Service simulator given to tool-using agents (copied next to plant.py as sim.py).

    python3 sim.py controller.py                      # every scenario, summarized
    python3 sim.py controller.py --scenario flash --trace

Runs your Controller against the simulated service under several demand
patterns and reports replicas, dropped requests and pod cost. It checks the
interface and bounds only; it does not grade the task requirements.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import sys

from plant import CONFIG, POD_CAPACITY, Sim, Workload

SCENARIOS = {
    "steady": (Workload(steps=[(0, 600.0)]), {}),
    "daily": (Workload(steps=[(0, 600.0)], amplitude=300.0, period=200), {}),
    "ramp": (Workload(steps=[(0, 300.0), (100, 1500.0)]), {"initial_replicas": 5}),
    "drop": (Workload(steps=[(0, 1200.0), (100, 300.0)]), {"initial_replicas": 20}),
    "flash": (Workload(steps=[(0, 400.0), (150, 1600.0), (180, 400.0)]), {"initial_replicas": 7}),
    "noisy": (Workload(steps=[(0, 600.0)], metric_noise=0.12, missing=0.1), {}),
}


def load(path: str) -> type:
    spec = importlib.util.spec_from_file_location("controller", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.Controller


def run(cls: type, name: str, seed: int, ticks: int) -> Sim:
    w, over = SCENARIOS[name]
    cfg = dict(CONFIG, **over)
    sim = Sim(cls(dict(cfg)), w, seed=seed, config=cfg).run(ticks)
    for r in sim.log:
        if not (cfg["min_replicas"] <= r["replicas"] <= cfg["max_replicas"]):
            raise ValueError(f"t={r['t']}: replicas {r['replicas']} outside bounds")
        if not isinstance(r["telemetry"], dict):
            raise TypeError(f"t={r['t']}: telemetry is not a dict")
    return sim


def summary(sim: Sim) -> dict:
    need = [max(1, math.ceil(r["demand"] / (POD_CAPACITY * CONFIG["target_utilization"]))) for r in sim.log]
    reps = [r["replicas"] for r in sim.log]
    return {"replicas_min": min(reps), "replicas_max": max(reps),
            "scaling_actions": sum(a != b for a, b in zip(reps, reps[1:])),
            "dropped_fraction": sum(r["dropped"] for r in sim.log) / max(1e-9, sum(r["demand"] for r in sim.log)),
            "cost_ratio": sum(reps) / sum(need)}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("controller")
    p.add_argument("--scenario", choices=sorted(SCENARIOS))
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--ticks", type=int, default=400)
    p.add_argument("--trace", action="store_true", help="print every tick as JSON lines")
    a = p.parse_args()
    cls = load(a.controller)
    for name in [a.scenario] if a.scenario else sorted(SCENARIOS):
        sim = run(cls, name, a.seed, a.ticks)
        if a.trace:
            for r in sim.log:
                print(json.dumps({k: r[k] for k in ("t", "demand", "ready", "replicas", "dropped")}))
        out = summary(sim)
        if name == "flash":
            rows = [r for r in sim.log if 150 <= r["t"] < 180]
            out["dropped_fraction_during_flash"] = sum(r["dropped"] for r in rows) / sum(r["demand"] for r in rows)
        print(name, json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
