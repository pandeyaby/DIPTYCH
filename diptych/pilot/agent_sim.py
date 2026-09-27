"""Closed-loop simulator given to tool-using agents (copied next to plant.py).

    python3 sim.py controller.py                 # all scenarios, summary
    python3 sim.py controller.py --scenario steady --trace

Runs your Controller against the service plant and reports latency tracking,
admitted load, limit range, and interface errors. It checks the interface and
bounds only; it does not grade the task requirements.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys

from plant import CONFIG, SCENARIOS, Plant


def load(path: str) -> type:
    spec = importlib.util.spec_from_file_location("controller", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.Controller


def run(cls: type, scenario: str, seed: int = 0) -> list[dict]:
    plant = Plant(SCENARIOS[scenario], seed=seed)
    ctrl = cls(dict(CONFIG))
    prev, out = CONFIG["initial_limit"], []
    for t in range(plant.scenario.length):
        obs = plant.observe(t, prev)
        d = ctrl.tick(dict(obs))
        lim = d["limit"]
        if not (CONFIG["min_limit"] <= lim <= CONFIG["max_limit"]):
            raise ValueError(f"t={t}: limit {lim} outside bounds")
        if not isinstance(d.get("telemetry"), dict):
            raise TypeError(f"t={t}: telemetry is not a dict")
        out.append({"t": t, "latency_ms": obs["latency_ms"], "setpoint_ms": obs["setpoint_ms"],
                    "admitted_rps": obs["admitted_rps"], "limit": lim})
        prev = lim
    return out


def summary(rows: list[dict]) -> dict:
    tail = rows[40:]
    err = [abs(r["latency_ms"] - r["setpoint_ms"]) / r["setpoint_ms"] for r in tail]
    lims = [r["limit"] for r in rows]
    return {"mean_abs_latency_error": sum(err) / len(err),
            "mean_admitted_rps": sum(r["admitted_rps"] for r in tail) / len(tail),
            "limit_min": min(lims), "limit_max": max(lims)}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("controller")
    p.add_argument("--scenario", choices=[s for s in SCENARIOS if s != "steady_long"])
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--trace", action="store_true", help="print every tick as JSON lines")
    a = p.parse_args()
    cls = load(a.controller)
    for sc in [a.scenario] if a.scenario else [s for s in SCENARIOS if s != "steady_long"]:
        rows = run(cls, sc, a.seed)
        if a.trace:
            for r in rows:
                print(json.dumps(r))
        print(sc, json.dumps(summary(rows)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
