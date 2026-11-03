"""Held-out deployment outcomes for RQ3 (predictive validity).

Pre-registered in ``paper/RQ3_PREREG.md``. For each task and each behavioral
requirement, a scenario that no probe, baseline, or control calibration uses
(new demand shapes, new path parameters, seeds 100-102), and an outcome that
measures the operational harm the requirement exists to prevent. Higher is
worse. Outcomes are computed from closed-loop runs only; no probe, predicate,
or verdict is consulted.

    python -m diptych.pilot.heldout --task congestion            # all artifacts + controls
    python -m diptych.pilot.heldout --task congestion --one PATH # one controller, JSON to stdout
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import math
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SEEDS = (100, 101, 102)


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _steps(points: list[tuple[int, float]]) -> tuple[tuple[int, float], ...]:
    return tuple(points)


# ---------------------------------------------------------------------------
# Task A: concurrency limiter. Outcome: fraction of ticks in the outcome
# window that are overloaded (latency > 1.5 x setpoint) or starving (latency
# < 0.5 x setpoint while arrivals exceed the limit).
# ---------------------------------------------------------------------------

def _concurrency_scenarios() -> dict[str, tuple[Any, tuple[int, int]]]:
    from diptych.pilot.plant import Scenario

    creep = [(0, 60.0)] + [(50 + 5 * i, 62.0 + 2 * i) for i in range(20)] + \
            [(220 + 5 * i, 98.0 - 2 * i) for i in range(20)]
    return {
        # requirement -> (scenario, outcome window)
        "SIGNFLIP": (Scenario("ho_surges", 260,
                              _steps([(0, 70.0), (60, 110.0), (80, 70.0), (140, 115.0), (155, 70.0),
                                      (200, 112.0), (215, 70.0)]),
                              ((0, 5.0),), ((0, 100.0),)), (40, 260)),
        "TRAJSWAP": (Scenario("ho_creep", 300, _steps(creep), ((0, 5.0),), ((0, 100.0),)), (50, 300)),
        "VARSCALE": (Scenario("ho_regimes", 300, ((0, 78.0),), ((0, 4.0), (100, 22.0), (200, 4.0)),
                              ((0, 100.0),)), (100, 200)),
        "SATEXTEND": (Scenario("ho_idle_rush", 220, ((0, 25.0), (160, 105.0)), ((0, 3.0),),
                               ((0, 100.0),)), (160, 190)),
        "HISTSWAP": (Scenario("ho_setpoints", 320, ((0, 80.0),), ((0, 8.0),),
                              ((0, 100.0), (80, 130.0), (160, 80.0), (240, 110.0))), (80, 320)),
    }


def concurrency_outcomes(cls: type) -> dict[str, float]:
    from diptych.pilot.plant import CONFIG, Plant
    from diptych.pilot.probes import Counted, run_closed_loop

    out = {}
    for req, (sc, (lo, hi)) in _concurrency_scenarios().items():
        vals = []
        for seed in SEEDS:
            trace: list[dict] = []
            run_closed_loop(Counted(cls(dict(CONFIG)), [0]), Plant(sc, seed=seed), 0, sc.length,
                            CONFIG["initial_limit"], trace)
            bad = 0
            for r in trace[lo:hi]:
                o = r["obs"]
                over = o["latency_ms"] > 1.5 * o["setpoint_ms"]
                starve = o["latency_ms"] < 0.5 * o["setpoint_ms"] and o["arrival_rps"] > o["admitted_rps"] + 1e-9
                bad += over or starve
            vals.append(bad / (hi - lo))
        out[req] = _mean(vals)
    return out


# ---------------------------------------------------------------------------
# Task B: congestion control. Outcomes per requirement (docstrings below).
# ---------------------------------------------------------------------------

def congestion_outcomes(cls: type) -> dict[str, float]:
    from diptych.pilot.congestion.plant import CONFIG, Flow, Path as NetPath, Sim
    from diptych.pilot.congestion.reference import Controller as Reno

    def cand() -> Any:
        return cls(dict(CONFIG))

    def series(flow: Any, key: str, a: int, b: int) -> list[float]:
        return [r[key] for r in flow.log if a <= r["t"] < b]

    def shared(seed: int) -> Any:
        # Two standard flows on a wider, longer path; the candidate joins third.
        sim = Sim([Flow(Reno(dict(CONFIG))), Flow(Reno(dict(CONFIG)), start=50), Flow(cand(), start=100)],
                  path=NetPath(capacity=15.0, base_rtt=3.0, buffer=45.0), seed=seed)
        return sim.run(800)

    def scheduled(seed: int, events: dict[int, Callable[[Any], None]], until: int = 800) -> Any:
        sim = Sim([Flow(cand())], seed=seed)
        for t in sorted(events) + [until]:
            sim.run(min(t, until))
            if t in events and t < until:
                events[t](sim.path)
        return sim

    out: dict[str, list[float]] = {k: [] for k in
                                   ("harm-throughput", "harm-latency", "short-flows", "delay-change", "path-change")}
    for seed in SEEDS:
        sim = shared(seed)
        fair = 15.0 / 3
        victims = _mean([_mean(series(f, "acked", 400, 800)) for f in sim.flows[:2]])
        out["harm-throughput"].append(max(0.0, 1.0 - victims / fair))       # standard flows' fair-share shortfall
        out["harm-latency"].append(_mean(sim.queue_log[400:800]) / 45.0)    # standing queue, fraction of buffer

        # Eight short standard flows arrive beside the established candidate.
        sim = Sim([Flow(cand())], seed=seed).run(200)
        shorts = [Flow(Reno(dict(CONFIG)), start=200 + 60 * i, size=100.0) for i in range(8)]
        sim.flows.extend(shorts)
        sim.run(200 + 60 * 8 + 300)
        out["short-flows"].append(_mean([(f.done_at - f.start + 1) if f.done_at is not None else 300.0
                                         for f in shorts]))                 # mean completion ticks

        # Base RTT moves repeatedly; capacity fixed.
        sim = scheduled(seed, {200: lambda p: setattr(p, "base_rtt", 4.0), 400: lambda p: setattr(p, "base_rtt", 3.0),
                               600: lambda p: setattr(p, "base_rtt", 6.0)})
        out["delay-change"].append(1.0 - _mean(series(sim.flows[0], "acked", 200, 800)) / 10.0)  # lost utilization

        # Capacity flaps; score each settled post-change window.
        caps = {200: 6.0, 400: 12.0, 600: 4.0}
        sim = scheduled(seed, {t: (lambda c: lambda p: setattr(p, "capacity", c))(c) for t, c in caps.items()})
        scores = []
        for t, c in caps.items():
            a, b = t + 100, t + 200
            acked = sum(series(sim.flows[0], "acked", a, b))
            lost = sum(series(sim.flows[0], "lost", a, b))
            loss = lost / (acked + lost) if acked + lost > 0 else 1.0
            queue = _mean(sim.queue_log[a:b]) / 20.0
            shortfall = max(0.0, 0.8 - acked / (100 * c))
            scores.append(loss + queue + shortfall)
        out["path-change"].append(_mean(scores))
    return {k: _mean(v) for k, v in out.items()}


# ---------------------------------------------------------------------------
# Task C: autoscaler.
# ---------------------------------------------------------------------------

def autoscale_outcomes(cls: type) -> dict[str, float]:
    from diptych.pilot.autoscale.plant import CONFIG, Sim, Workload
    from diptych.pilot.autoscale.probes import oracle

    def run(w: Any, seed: int, ticks: int, startup: int = 3, **cfg: Any) -> Any:
        config = dict(CONFIG, **cfg)
        return Sim(cls(dict(config)), w, seed=seed, config=config, startup=startup).run(ticks)

    out: dict[str, list[float]] = {k: [] for k in
                                   ("missing-metrics", "not-ready", "window-history", "ratio-only", "flapping")}
    for seed in SEEDS:
        # Metrics pipeline degraded: 30% of ready pods report nothing.
        sim = run(Workload(steps=[(0, 650.0)], amplitude=250.0, period=160, missing=0.3), seed, 320)
        out["missing-metrics"].append(_mean([abs(r["replicas"] - oracle(r["demand"])) / oracle(r["demand"])
                                             for r in sim.log[40:]]))      # mis-provisioning
        # Slow-starting pods (6 ticks) during a ramp.
        sim = run(Workload(steps=[(0, 300.0), (100, 1200.0), (110, 1800.0)]), seed, 200, startup=6,
                  initial_replicas=5)
        need = oracle(1800.0)
        out["not-ready"].append(sum(max(0, r["replicas"] - 1.2 * need) for r in sim.log[100:200]) / 100)  # overshoot
        # Spikes followed by long quiet periods: pods kept long after the window.
        steps = [(0, 500.0)]
        for s in (50, 150, 250):
            steps += [(s, 1500.0), (s + 10, 500.0)]
        sim = run(Workload(steps=steps), seed, 350)
        quiet = [r for r in sim.log if any(s + 10 + 30 <= r["t"] < s + 100 for s in (50, 150, 250))]
        out["window-history"].append(_mean([max(0, r["replicas"] - oracle(500.0) - 1) for r in quiet]))
        # A different target.
        sim = run(Workload(steps=[(0, 700.0)], amplitude=300.0, period=180), seed, 360, target_utilization=0.75)
        out["ratio-only"].append(_mean([abs(r["replicas"] - oracle(r["demand"], 0.75)) / oracle(r["demand"], 0.75)
                                        for r in sim.log[40:]]))
        # Noisy, partly missing metrics under steady load.
        sim = run(Workload(steps=[(0, 700.0)], metric_noise=0.15, missing=0.05), seed, 400)
        reps = [r["replicas"] for r in sim.log]
        out["flapping"].append(100 * sum(a != b for a, b in zip(reps, reps[1:])) / len(reps))  # actions / 100 ticks
    return {k: _mean(v) for k, v in out.items()}


OUTCOMES: dict[str, Callable[[type], dict[str, float]]] = {
    "concurrency": concurrency_outcomes,
    "congestion": congestion_outcomes,
    "autoscale": autoscale_outcomes,
}
RESULTS = {"concurrency": "heldout.json", "congestion": "heldout_congestion.json",
           "autoscale": "heldout_autoscale.json"}


def one(task: str, path: str) -> dict[str, Any]:
    from diptych.pilot.tasks import TASKS

    probes = TASKS[task]._probes() if task != "concurrency" else __import__(
        "diptych.pilot.probes", fromlist=["load_controller_class"])
    try:
        cls = probes.load_controller_class(path)
        return {"outcomes": OUTCOMES[task](cls)}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}


def _isolated(task: str, path: str, timeout: int = 600) -> dict[str, Any]:
    try:
        proc = subprocess.run([sys.executable, "-m", "diptych.pilot.heldout", "--task", task, "--one", path],
                              capture_output=True, text=True, timeout=timeout, cwd=ROOT, check=False)
        return json.loads(proc.stdout) if proc.returncode == 0 else {"error": proc.stderr[-300:]}
    except subprocess.TimeoutExpired:
        return {"error": "timed out"}
    except json.JSONDecodeError:
        return {"error": "no JSON from worker"}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="diptych.pilot.heldout")
    p.add_argument("--task", choices=sorted(OUTCOMES), required=True)
    p.add_argument("--one", help="evaluate one controller and print JSON")
    p.add_argument("--controls-only", action="store_true")
    p.add_argument("--jobs", type=int, default=6)
    args = p.parse_args(argv)
    if args.one:
        print(json.dumps(one(args.task, args.one)))
        return 0
    import diptych.pilot.study as study
    from diptych.pilot.tasks import TASKS

    study.TASK = TASKS[args.task]
    targets = [(n, path) for n, path, _ in study._controls()]
    if not args.controls_only:
        targets += [(n, path) for n, path, _ in study._artifacts()]
    with cf.ThreadPoolExecutor(max_workers=args.jobs) as ex:
        rows = list(ex.map(lambda t: {"name": t[0], **_isolated(args.task, t[1])}, targets))
    out = HERE / ("heldout_controls_" + args.task + ".json" if args.controls_only else RESULTS[args.task])
    out.write_text(json.dumps({"task": args.task, "seeds": list(SEEDS), "rows": rows}, indent=2) + "\n")
    for r in rows:
        print(f"{r['name']:<46}", r.get("error") or {k: round(v, 3) for k, v in r["outcomes"].items()})
    return 0


if __name__ == "__main__":
    sys.exit(main())
