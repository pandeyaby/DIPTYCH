"""Paired probes, single-run checks and baselines for the autoscaling task.

Same structure as ``diptych.pilot.congestion.probes``: a probe RECORDS a pair
of executions into a live envelope; the requirement predicate grades it
through the shared grader. The three requirements the HPA documentation
states against a formula or a fixed limit are single-run checks.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import math
import os
from pathlib import Path as FsPath
from typing import Any, Callable

from diptych.grade import grade_document
from diptych.live import live_envelope, twin
from diptych.pilot.autoscale.plant import CONFIG, POD_CAPACITY, Sim, Workload
from diptych.pilot.autoscale.predicates import PAIRED, SINGLE_RUN, TASK

KEYS: tuple[str, ...] = SINGLE_RUN + PAIRED
SEEDS = (0, 1, 2)
TARGET = CONFIG["target_utilization"]
WINDOW = int(CONFIG["stabilization_ticks"])


def scenarios() -> dict[str, tuple[Workload, dict]]:
    """name -> (workload, config overrides)."""
    return {
        "steady": (Workload(steps=[(0, 600.0)]), {}),
        "diurnal": (Workload(steps=[(0, 600.0)], amplitude=300.0, period=200), {}),
        "step_up": (Workload(steps=[(0, 300.0), (100, 1500.0)]), {"initial_replicas": 5}),
        "step_down": (Workload(steps=[(0, 1200.0), (100, 300.0)]), {"initial_replicas": 20}),
        "spike": (Workload(steps=[(0, 600.0), (40, 1500.0), (60, 600.0)]), {}),
        "flash": (Workload(steps=[(0, 400.0), (150, 1600.0), (180, 400.0)]), {"initial_replicas": 7}),
    }


class ArtifactError(Exception):
    """The artifact raised or returned an ill-formed decision."""


def load_controller_class(path: str | FsPath) -> type:
    path = FsPath(path)
    spec = importlib.util.spec_from_file_location(f"as_artifact_{abs(hash(str(path)))}", path)
    if spec is None or spec.loader is None:
        raise ArtifactError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    cls = getattr(mod, "Controller", None)
    if cls is None:
        raise ArtifactError("module defines no Controller")
    return cls


class Counted:
    """Wrap a candidate autoscaler; count and validate every tick."""

    def __init__(self, ctrl: Any, counter: list[int]):
        self.ctrl = ctrl
        self.counter = counter

    def tick(self, obs: dict) -> dict:
        self.counter[0] += 1
        try:
            d = self.ctrl.tick(copy.deepcopy(obs))
        except Exception as exc:  # noqa: BLE001
            raise ArtifactError(f"tick raised {type(exc).__name__}: {exc}") from exc
        if not isinstance(d, dict) or "replicas" not in d:
            raise ArtifactError("decision is not a dict with 'replicas'")
        r = d["replicas"]
        if isinstance(r, bool) or not isinstance(r, (int, float)) or not math.isfinite(r):
            raise ArtifactError(f"replicas not a finite number: {r!r}")
        tel = d.get("telemetry", {})
        if not isinstance(tel, dict):
            raise ArtifactError("telemetry is not a dict")
        return {"replicas": int(r), "telemetry": tel}

    def __deepcopy__(self, memo: dict) -> "Counted":
        try:
            return Counted(copy.deepcopy(self.ctrl, memo), self.counter)
        except Exception as exc:  # noqa: BLE001
            raise ArtifactError(f"state not copyable: {exc}") from exc


def _sim(cls: type, counter: list[int], name: str, seed: int, workload: Workload | None = None,
         **cfg: Any) -> Sim:
    w, over = scenarios()[name]
    config = dict(CONFIG, **over, **cfg)
    try:
        ctrl = Counted(cls(dict(config)), counter)
    except Exception as exc:  # noqa: BLE001
        raise ArtifactError(f"constructor raised {type(exc).__name__}: {exc}") from exc
    return Sim(ctrl, workload or w, seed=seed, config=config)


def _replicas(sim: Sim, start: int = 0, stop: int | None = None) -> list[int]:
    return [r["replicas"] for r in sim.log[start:stop]]


def oracle(demand: float, target: float = TARGET) -> int:
    return max(1, math.ceil(demand / (POD_CAPACITY * target) - 1e-9))


# ---------------------------------------------------------------------------
# Paired probes (recorders)
# ---------------------------------------------------------------------------

def _envelope(req: str, op: str, coupling: str, ctx: dict, twins: list[dict], **meta: Any) -> dict:
    return live_envelope(
        task=TASK, requirement=req, operator=op, coupling=coupling, artifact=ctx.get("artifact", ""),
        probe_id=f"live.{TASK}.{req}.{ctx.get('scenario', 'x')}.s{ctx['seed']}.t{ctx.get('t_p', 0)}",
        twins=twins, fork_tick=ctx.get("t_p"), **meta,
    )


def _one_tick(sim: Sim, obs: dict) -> int:
    branch = copy.deepcopy(sim)
    return branch.step(obs)


# Fork points: (scenario, tick).
MISSING_FORKS = (("diurnal", 40), ("diurnal", 140), ("step_up", 101), ("step_down", 121))
UNREADY_FORKS = (("step_up", 101), ("step_up", 102), ("flash", 151), ("flash", 152))


def record_missing(sim: Sim, ctx: dict) -> dict:
    """SIGNFLIP-style one-tick pair: all metrics (a) / every third ready pod's
    metric withheld (b)."""
    obs = sim.observe()
    held = copy.deepcopy(obs)
    k = 0
    for p in held["pods"]:
        if p["ready"] and p["utilization"] is not None:
            if k % 3 == 0:
                p["utilization"] = None
            k += 1
    n = obs["replicas"]
    twins = [twin(name, {"replicas": [n, _one_tick(sim, o)]}, ctx["seed"], arm=name)
             for name, o in (("all_metrics", obs), ("some_missing", held))]
    return _envelope("missing-metrics", "SIGNFLIP", "open_loop", ctx, twins)


def record_unready(sim: Sim, ctx: dict) -> dict:
    """One-tick pair: starting pods not ready (a) / the same pods ready and
    reporting 0% (b)."""
    obs = sim.observe()
    idle = copy.deepcopy(obs)
    unready = 0
    for p in idle["pods"]:
        if not p["ready"]:
            p["ready"], p["utilization"] = True, 0.0
            unready += 1
    n = obs["replicas"]
    twins = [twin(name, {"replicas": [n, _one_tick(sim, o)]}, ctx["seed"], arm=name)
             for name, o in (("unready", obs), ("ready_idle", idle))]
    return _envelope("not-ready", "SIGNFLIP", "open_loop", ctx, twins, unready=unready)


HIST_T0 = 60
HIST_FROM = HIST_T0 + WINDOW + 10
HIST_TO = 200
HIST_REPLICAS = 12
HIST_UTIL = 0.47      # ratio 0.78: outside the tolerance, 12 * 0.78 = 9.4 -> 10


def scripted_obs(t: int, util: float, rng_z: list[float], n: int = HIST_REPLICAS) -> dict:
    """An open-loop observation: n ready pods around ``util``."""
    return {"t": t, "replicas": n,
            "pods": [{"ready": True, "utilization": max(0.0, util * (1.0 + 0.03 * z))} for z in rng_z[:n]]}


def record_window_history(cls: type, counter: list[int], ctx: dict) -> dict:
    """HISTSWAP, open loop: the same scripted observations from t0 on (12
    pods at utilization 0.47), after a spike to 1.2 (a) or no spike (b) before
    t0. Decisions are graded, not fed back, so only the autoscaler's memory
    can carry the spike past the stabilization window."""
    import random

    seed = ctx["seed"]
    rng = random.Random(seed)
    zs = [[rng.gauss(0.0, 1.0) for _ in range(HIST_REPLICAS)] for _ in range(HIST_TO)]
    twins = []
    for name, spike in (("spike", 1.2), ("no_spike", HIST_UTIL)):
        try:
            ctrl = Counted(cls(dict(CONFIG)), counter)
        except Exception as exc:  # noqa: BLE001
            raise ArtifactError(f"constructor raised {type(exc).__name__}: {exc}") from exc
        out = []
        for t in range(HIST_TO):
            u = spike if 40 <= t < HIST_T0 else HIST_UTIL
            out.append(ctrl.tick(scripted_obs(t, u, zs[t]))["replicas"])
        twins.append(twin(name, {"replicas": out[HIST_FROM:]}, seed, history=name))
    return _envelope("window-history", "HISTSWAP", "open_loop", dict(ctx, t_p=HIST_T0), twins)


RATIO_LEN = 300


def record_ratio(cls: type, counter: list[int], ctx: dict) -> dict:
    """RESCALE: target 0.6 (a) / target 0.9 with every demand 1.5x (b)."""
    seed = ctx["seed"]
    twins = []
    for name, target, scale in (("target_0.6", 0.6, 1.0), ("target_0.9", 0.9, 1.5)):
        w = copy.deepcopy(scenarios()["diurnal"][0])
        w.scale = scale
        sim = _sim(cls, counter, "diurnal", seed, workload=w, target_utilization=target).run(RATIO_LEN)
        twins.append(twin(name, {"replicas": _replicas(sim)}, seed, crn_stream_id=f"replicas:{seed}",
                          crn_closed_loop=True, target=target, demand_scale=scale))
    return _envelope("ratio-only", "RESCALE", "crn_closed_loop", ctx, twins)


FLAP_LEN = 400
FLAP_NOISE = (0.02, 0.12)


def record_flapping(cls: type, counter: list[int], ctx: dict) -> dict:
    """VARSCALE: steady demand, the same draws, metric noise 0.02 (a) / 0.12 (b)."""
    seed = ctx["seed"]
    twins = []
    for noise in FLAP_NOISE:
        w = Workload(steps=[(0, 600.0)], metric_noise=noise)
        sim = _sim(cls, counter, "steady", seed, workload=w).run(FLAP_LEN)
        twins.append(twin(f"noise_{noise}", {"replicas": _replicas(sim)}, seed,
                          crn_stream_id=f"replicas:{seed}", crn_closed_loop=True, metric_noise=noise))
    return _envelope("flapping", "VARSCALE", "crn_closed_loop", ctx, twins)


def _save(envelope: dict, ctx: dict) -> None:
    out = os.environ.get("PILOT_ENVELOPE_DIR")
    if out:
        d = FsPath(out) / FsPath(str(ctx.get("artifact") or "artifact")).with_suffix("").name
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{envelope['probe_id']}.json").write_text(json.dumps(envelope, sort_keys=True))


def _grade(envelope: dict, ctx: dict) -> tuple[str, dict]:
    _save(envelope, ctx)
    res = grade_document(envelope)
    ev = dict(res.evidence)
    if res.actual_verdict == "inconclusive":
        ev.setdefault("reason", res.reason)
    return res.actual_verdict, ev


def _combine(verdicts: list[str]) -> str:
    for v in ("error", "fail", "pass"):
        if v in verdicts:
            return v
    return "inconclusive"


def run_paired(cls: type, seed: int, artifact: str = "") -> tuple[dict[str, Any], dict[str, int]]:
    """All paired probes at one seed: verdict per requirement, tick counts."""
    counter = [0]
    naive = 0
    prefix_shared = 0
    per: dict[str, list[dict]] = {k: [] for k in PAIRED}
    ctx0 = {"seed": seed, "artifact": artifact}

    def add(req: str, fn: Callable[[], dict], ctx: dict) -> None:
        try:
            v, ev = _grade(fn(), ctx)
        except ArtifactError as exc:
            v, ev = "error", {"reason": str(exc)}
        per[req].append({"scenario": ctx.get("scenario"), "t_p": ctx.get("t_p"), "verdict": v, "evidence": ev})

    # One-tick forks share one prefix run per scenario.
    forks: dict[str, list[tuple[str, int]]] = {}
    for req, points in (("missing-metrics", MISSING_FORKS), ("not-ready", UNREADY_FORKS)):
        for scen, t in points:
            forks.setdefault(scen, []).append((req, t))
    for scen, points in forks.items():
        try:
            sim = _sim(cls, counter, scen, seed)
        except ArtifactError as exc:
            for req, t in points:
                per[req].append({"scenario": scen, "t_p": t, "verdict": "error", "evidence": {"reason": str(exc)}})
            continue
        for req, t in sorted(points, key=lambda p: p[1]):
            start = counter[0]
            try:
                sim.run(t)
            except ArtifactError as exc:
                per[req].append({"scenario": scen, "t_p": t, "verdict": "error", "evidence": {"reason": str(exc)}})
                continue
            prefix_shared += counter[0] - start
            c = dict(ctx0, scenario=scen, t_p=t)
            before = counter[0]
            add(req, (lambda: record_missing(sim, c)) if req == "missing-metrics" else (lambda: record_unready(sim, c)), c)
            naive += 2 * t + (counter[0] - before)   # without sharing, each arm replays the prefix
    for req, fn in (("window-history", record_window_history), ("ratio-only", record_ratio),
                    ("flapping", record_flapping)):
        before = counter[0]
        add(req, lambda: fn(cls, counter, ctx0), dict(ctx0, scenario=req))
        naive += counter[0] - before                 # two runs from scratch: nothing to share
    out = {req: {"verdict": _combine([r["verdict"] for r in rows]), "forks": rows} for req, rows in per.items()}
    return out, {"forked": counter[0], "prefix_shared": prefix_shared, "naive_equivalent": naive}


def fork_fidelity(cls: type, seed: int) -> dict[str, Any]:
    """Forking the simulation must equal running it straight through."""
    t_p = 120
    forked = copy.deepcopy(_sim(cls, [0], "diurnal", seed).run(t_p)).run(t_p + 100)
    replayed = _sim(cls, [0], "diurnal", seed).run(t_p + 100)
    return {"t_p": t_p, "ok": _replicas(forked, t_p) == _replicas(replayed, t_p)}


# ---------------------------------------------------------------------------
# Single-run requirements
# ---------------------------------------------------------------------------

def check_tolerance(cls: type, seed: int) -> tuple[str, dict]:
    """Utilization 5% above target throughout: no scaling action."""
    w = Workload(steps=[(0, 10 * POD_CAPACITY * TARGET * 1.05)], metric_noise=0.02)
    sim = _sim(cls, [0], "steady", seed, workload=w).run(150)
    changes = sum(r["replicas"] != r["replicas_before"] for r in sim.log)
    return ("pass" if changes == 0 else "fail"), {"scaling_actions": changes}


def check_scale_up_rate(cls: type, seed: int) -> tuple[str, dict]:
    """Each tick, at most max(+4 pods, +100%)."""
    sim = _sim(cls, [0], "step_up", seed).run(160)
    worst = [r for r in sim.log if r["replicas"] > max(2 * r["replicas_before"], r["replicas_before"] + 4)]
    return ("pass" if not worst else "fail"), {"violations": len(worst),
                                               "first": {k: worst[0][k] for k in ("t", "replicas_before", "replicas")}
                                               if worst else None}


def base_recommendation(obs: dict, target: float = TARGET, tol: float = CONFIG["tolerance"]) -> int:
    """The documentation's formula over ready pods with metrics."""
    u = [p["utilization"] for p in obs["pods"] if p["ready"] and p["utilization"] is not None]
    n = obs["replicas"]
    if not u:
        return n
    ratio = (sum(u) / len(u)) / target
    return n if abs(ratio - 1.0) <= tol else math.ceil(len(u) * ratio)


def check_downscale_stabilization(cls: type, seed: int) -> tuple[str, dict]:
    """A scale-down never goes below the highest recommendation in the window."""
    sim = _sim(cls, [0], "step_down", seed).run(200)
    recs = [base_recommendation(r["obs"]) for r in sim.log]
    bad = []
    for i, r in enumerate(sim.log):
        if r["replicas"] < r["replicas_before"]:
            floor = min(r["replicas_before"], max(recs[max(0, i - WINDOW + 1):i + 1]))
            if r["replicas"] < floor:
                bad.append({"t": r["t"], "replicas": r["replicas"], "floor": floor})
    return ("pass" if not bad else "fail"), {"violations": len(bad), "first": bad[0] if bad else None}


SINGLE_RUN_CHECKS: dict[str, Callable[[type, int], tuple[str, dict]]] = {
    "tolerance": check_tolerance,
    "scale-up-rate": check_scale_up_rate,
    "downscale-stabilization": check_downscale_stabilization,
}


# ---------------------------------------------------------------------------
# Single-trace baselines for the paired requirements: one run each.
# ---------------------------------------------------------------------------

def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _missing_run(cls: type, seed: int) -> Sim:
    w = Workload(steps=[(0, 600.0)], amplitude=300.0, period=200, missing=0.2)
    return _sim(cls, [0], "diurnal", seed, workload=w).run(300)


def _deltas(sim: Sim, missing: bool) -> list[int]:
    out = []
    for r in sim.log:
        has = any(p["ready"] and p["utilization"] is None for p in r["obs"]["pods"])
        if has == missing:
            out.append(abs(r["replicas"] - r["replicas_before"]))
    return out


def _b_missing_mean(cls: type, seed: int) -> bool:
    sim = _missing_run(cls, seed)
    return _mean(_deltas(sim, True)) <= _mean(_deltas(sim, False)) + 0.5


def _b_missing_max(cls: type, seed: int) -> bool:
    sim = _missing_run(cls, seed)
    return max(_deltas(sim, True), default=0) <= max(_deltas(sim, False), default=0) + 1


def _b_unready_overshoot(cls: type, seed: int) -> bool:
    sim = _sim(cls, [0], "step_up", seed).run(160)
    return max(_replicas(sim, 100)) <= 1.25 * oracle(1500.0)


def _b_unready_time_above(cls: type, seed: int) -> bool:
    sim = _sim(cls, [0], "step_up", seed).run(160)
    above = [x for x in _replicas(sim, 100) if x > oracle(1500.0) + 1]
    return len(above) <= 6


def _spike_decisions(cls: type, seed: int) -> list[int]:
    import random

    rng = random.Random(seed)
    ctrl = cls(dict(CONFIG))
    out = []
    for t in range(HIST_TO):
        z = [rng.gauss(0.0, 1.0) for _ in range(HIST_REPLICAS)]
        out.append(int(ctrl.tick(scripted_obs(t, 1.2 if 40 <= t < HIST_T0 else HIST_UTIL, z))["replicas"]))
    return out[HIST_FROM:]


def _b_history_oracle(cls: type, seed: int) -> bool:
    """Long after the spike, decisions match the documented formula."""
    return all(x == math.ceil(HIST_REPLICAS * HIST_UTIL / TARGET) for x in _spike_decisions(cls, seed))


def _b_history_quiet(cls: type, seed: int) -> bool:
    return len(set(_spike_decisions(cls, seed))) == 1


def _b_ratio_settled(cls: type, seed: int) -> bool:
    sim = _sim(cls, [0], "steady", seed).run(200)
    return all(abs(x - oracle(600.0)) <= 1 for x in _replicas(sim, 100))


def _b_ratio_tracking(cls: type, seed: int) -> bool:
    sim = _sim(cls, [0], "diurnal", seed).run(RATIO_LEN)
    ok = [abs(r["replicas"] - oracle(r["demand"])) <= 2 for r in sim.log[50:]]
    return sum(ok) >= 0.9 * len(ok)


def _noisy(cls: type, seed: int) -> list[int]:
    w = Workload(steps=[(0, 600.0)], metric_noise=FLAP_NOISE[1])
    return _replicas(_sim(cls, [0], "steady", seed, workload=w).run(FLAP_LEN))


def _b_flap_reversals(cls: type, seed: int) -> bool:
    from diptych.pilot.autoscale.predicates import _reversals
    return _reversals(_noisy(cls, seed)) <= 3


def _b_flap_actions(cls: type, seed: int) -> bool:
    xs = _noisy(cls, seed)
    return sum(a != b for a, b in zip(xs, xs[1:])) <= 6


Reading = Callable[[type, int], bool]
BASIC: dict[str, Reading] = {
    "missing-metrics": _b_missing_mean,
    "not-ready": _b_unready_overshoot,
    "window-history": _b_history_oracle,
    "ratio-only": _b_ratio_settled,
    "flapping": _b_flap_reversals,
}
STRONG: dict[str, list[tuple[str, Reading]]] = {
    "missing-metrics": [("mean-step", _b_missing_mean), ("max-step", _b_missing_max)],
    "not-ready": [("overshoot", _b_unready_overshoot), ("time-above", _b_unready_time_above)],
    "window-history": [("oracle", _b_history_oracle), ("quiet", _b_history_quiet)],
    "ratio-only": [("settled", _b_ratio_settled), ("tracking", _b_ratio_tracking)],
    "flapping": [("reversals", _b_flap_reversals), ("actions", _b_flap_actions)],
}
STRONG_EXCLUDED: set[tuple[str, str]] = set()
_calibrated = False


def calibrate_strong() -> set[tuple[str, str]]:
    """Exclude strong readings that flag the conforming control at any seed."""
    global _calibrated
    conf = load_controller_class(FsPath(__file__).resolve().parent / "controls" / "conforming.py")
    STRONG_EXCLUDED.clear()
    for req, readings in STRONG.items():
        for name, fn in readings:
            if not all(fn(conf, s) for s in SEEDS):
                STRONG_EXCLUDED.add((req, name))
    _calibrated = True
    return set(STRONG_EXCLUDED)


def _single_run_vector(cls: type) -> dict[str, str]:
    return {req: _combine([check(cls, s)[0] for s in SEEDS]) for req, check in SINGLE_RUN_CHECKS.items()}


def single_trace_grade(cls: type, shared: dict[str, str]) -> dict[str, str]:
    out = dict(shared)
    for req, fn in BASIC.items():
        out[req] = "pass" if all(fn(cls, s) for s in SEEDS) else "fail"
    return out


def single_trace_grade_strong(cls: type, shared: dict[str, str]) -> dict[str, str]:
    if not _calibrated:
        calibrate_strong()
    out = dict(shared)
    for req, readings in STRONG.items():
        ok = all(fn(cls, s) for name, fn in readings if (req, name) not in STRONG_EXCLUDED for s in SEEDS)
        out[req] = "pass" if ok else "fail"
    return out


GOAL_DROPPED = 0.03     # fraction of requests dropped during a flash crowd
GOAL_COST = 1.3         # mean replicas / mean minimum needed at target, diurnal


def objective(cls: type) -> dict[str, Any]:
    """The task's two performance goals (not graded requirements)."""
    flash = _sim(cls, [0], "flash", 0).run(240)
    rows = [r for r in flash.log if 150 <= r["t"] < 180]   # the 7.5 minutes of high demand
    dropped = sum(r["dropped"] for r in rows) / sum(r["demand"] for r in rows)
    di = _sim(cls, [0], "diurnal", 0).run(400)
    cost = _mean([r["replicas"] for r in di.log[50:]]) / _mean([oracle(r["demand"]) for r in di.log[50:]])
    return {"flash_dropped_fraction": dropped, "diurnal_cost_ratio": cost,
            "meets_flash_goal": dropped <= GOAL_DROPPED, "meets_cost_goal": cost <= GOAL_COST}


def evaluate(artifact: str, seeds: tuple[int, ...] = SEEDS) -> dict[str, Any]:
    """Full evaluation of one autoscaling artifact."""
    result: dict[str, Any] = {"artifact": artifact, "task": TASK, "paired": {}, "single_trace": {},
                              "single_trace_strong": {}, "errors": []}
    try:
        cls = load_controller_class(artifact)
    except Exception as exc:  # noqa: BLE001
        result["load_error"] = f"{type(exc).__name__}: {exc}"
        for k in ("paired", "single_trace", "single_trace_strong"):
            result[k] = {key: "error" for key in KEYS}
        return result
    try:
        shared = _single_run_vector(cls)
    except Exception as exc:  # noqa: BLE001
        result["errors"].append(f"single_run: {type(exc).__name__}: {exc}")
        shared = {req: "error" for req in SINGLE_RUN}
    result["paired"].update(shared)
    try:
        per_seed, ticks, fid = {}, {"forked": 0, "prefix_shared": 0, "naive_equivalent": 0}, []
        for seed in seeds:
            out, t = run_paired(cls, seed, artifact)
            per_seed[seed] = out
            for k in ticks:
                ticks[k] += t[k]
            fid.append(fork_fidelity(cls, seed))
        result["paired_by_seed"] = {str(s): {r: o[r]["verdict"] for r in PAIRED} for s, o in per_seed.items()}
        result["paired"].update({r: _combine([per_seed[s][r]["verdict"] for s in seeds]) for r in PAIRED})
        result["paired_evidence"] = per_seed[seeds[0]]
        result["ticks"] = ticks
        result["fork_fidelity"] = {"ok": all(f["ok"] for f in fid), "checks": fid}
    except Exception as exc:  # noqa: BLE001
        result["errors"].append(f"paired: {type(exc).__name__}: {exc}")
        result["paired"].update({r: "error" for r in PAIRED})
    for key, fn in (("single_trace", single_trace_grade), ("single_trace_strong", single_trace_grade_strong)):
        try:
            result[key] = fn(cls, shared)
        except Exception as exc:  # noqa: BLE001
            result["errors"].append(f"{key}: {type(exc).__name__}: {exc}")
            result[key] = {**shared, **{r: "error" for r in PAIRED}}
    result["strong_excluded"] = sorted("/".join(k) for k in STRONG_EXCLUDED)
    try:
        result["objective"] = objective(cls)
    except Exception as exc:  # noqa: BLE001
        result["errors"].append(f"objective: {type(exc).__name__}: {exc}")
    for key in ("paired", "single_trace", "single_trace_strong"):
        result[key] = {k: result[key].get(k, "error") for k in KEYS}
    return result
