"""Bottleneck simulator given to tool-using agents (copied next to plant.py
and reference.py as sim.py).

    python3 sim.py controller.py            # solo run and a run beside a standard flow
    python3 sim.py controller.py --trace    # also print the solo run tick by tick

Runs your Controller on the simulated link, alone and sharing it with a
standard (Reno-style) flow, and reports throughput, loss, queue and window
range. It checks the interface and bounds only; it does not grade the task
requirements.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys

from plant import CONFIG, Flow, Sim
from reference import Controller as Standard


def load(path: str) -> type:
    spec = importlib.util.spec_from_file_location("controller", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.Controller


def summary(sim: Sim, flow: Flow, start: int) -> dict:
    rows = [r for r in flow.log if r["t"] >= start]
    sent = sum(r["acked"] + r["lost"] for r in rows)
    w = [r["cwnd"] for r in rows]
    return {"mean_throughput": sum(r["acked"] for r in rows) / len(rows),
            "loss_rate": sum(r["lost"] for r in rows) / sent if sent else 0.0,
            "mean_queue": sum(sim.queue_log[start:]) / len(sim.queue_log[start:]),
            "cwnd_min": min(w), "cwnd_max": max(w)}


def check(flow: Flow) -> None:
    for r in flow.log:
        if not (CONFIG["min_cwnd"] <= r["cwnd"] <= CONFIG["max_cwnd"]):
            raise ValueError(f"t={r['t']}: cwnd {r['cwnd']} outside bounds")
        if not isinstance(r["telemetry"], dict):
            raise TypeError(f"t={r['t']}: telemetry is not a dict")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("controller")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--ticks", type=int, default=600)
    p.add_argument("--trace", action="store_true", help="print every tick of the solo run as JSON lines")
    a = p.parse_args()
    cls = load(a.controller)
    solo = Sim([Flow(cls(dict(CONFIG)))], seed=a.seed).run(a.ticks)
    check(solo.flows[0])
    if a.trace:
        for r in solo.flows[0].log:
            print(json.dumps({k: r[k] for k in ("t", "acked", "lost", "rtt", "cwnd")}))
    print("solo", json.dumps(summary(solo, solo.flows[0], 200)))
    shared = Sim([Flow(Standard(dict(CONFIG))), Flow(cls(dict(CONFIG)), start=100)], seed=a.seed).run(a.ticks + 100)
    check(shared.flows[1])
    print("beside_standard.yours", json.dumps(summary(shared, shared.flows[1], 300)))
    print("beside_standard.standard", json.dumps(summary(shared, shared.flows[0], 300)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
