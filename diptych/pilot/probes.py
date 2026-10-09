"""Paired (hyperproperty) probes and a single-trace grader on live controllers.

Paired probes execute a shared prefix once in the closed-loop plant, fork the
controller with ``copy.deepcopy`` at tick ``t_p``, and drive the two branches
with inputs that differ on one axis only. Every ``tick`` call is counted, so
the cost of prefix sharing is measured, not assumed.

The single-trace grader reads the same eight requirements the way a harness
author reading the prose would: each check inspects one closed-loop run.

Verdicts: ``pass`` | ``fail`` | ``inconclusive`` (pair left the comparable
region, e.g. a bound was hit) | ``error`` (the artifact raised or returned an
ill-formed decision).
"""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import math
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

from diptych import OPERATORS
from diptych.grade import grade_document
from diptych.live import live_envelope, twin
from diptych.pilot.plant import CONFIG, SCENARIOS, Plant
from diptych.pilot.predicates import REQUIREMENT, TASK

FORK_POINTS = (80, 140, 200)
BOUND_EPS = 1e-9
SAT_SHORT, SAT_LONG = 5, 165


class ArtifactError(Exception):
    """The artifact raised or returned an ill-formed decision."""


def load_controller_class(path: str | Path) -> type:
    path = Path(path)
    spec = importlib.util.spec_from_file_location(f"pilot_artifact_{abs(hash(str(path)))}", path)
    if spec is None or spec.loader is None:
        raise ArtifactError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    cls = getattr(mod, "Controller", None)
    if cls is None:
        raise ArtifactError("module defines no Controller")
    return cls


class Counted:
    """Wrap a controller; count and validate every tick."""

    def __init__(self, ctrl: Any, counter: list[int]):
        self.ctrl = ctrl
        self.counter = counter

    def tick(self, obs: dict) -> dict:
        self.counter[0] += 1
        try:
            d = self.ctrl.tick(dict(obs))
        except Exception as exc:  # noqa: BLE001
            raise ArtifactError(f"tick raised {type(exc).__name__}: {exc}") from exc
        if not isinstance(d, dict) or "limit" not in d:
            raise ArtifactError("decision is not a dict with 'limit'")
        lim = d["limit"]
        if not isinstance(lim, (int, float)) or isinstance(lim, bool) or not math.isfinite(lim):
            raise ArtifactError(f"limit not a finite number: {lim!r}")
        tel = d.get("telemetry")
        if not isinstance(tel, dict):
            raise ArtifactError("telemetry is not a dict")
        return {"limit": float(lim), "telemetry": tel}

    def fork(self) -> "Counted":
        try:
            return Counted(copy.deepcopy(self.ctrl), self.counter)
        except Exception as exc:  # noqa: BLE001
            raise ArtifactError(f"state not copyable: {exc}") from exc


def run_closed_loop(ctrl: Counted, plant: Plant, start: int, stop: int, prev_limit: float,
                    trace: list[dict] | None = None) -> float:
    for t in range(start, stop):
        obs = plant.observe(t, prev_limit)
        d = ctrl.tick(obs)
        prev_limit = d["limit"]
        if trace is not None:
            trace.append({"obs": obs, "limit": d["limit"], "telemetry": d["telemetry"]})
    return prev_limit


def _feed(ctrl: Counted, base: dict, latencies: list[float], setpoint: float | None = None) -> list[float]:
    out = []
    t = int(base["t"])
    for lat in latencies:
        t += 1
        obs = dict(base, t=t, latency_ms=lat)
        if setpoint is not None:
            obs["setpoint_ms"] = setpoint
        out.append(ctrl.tick(obs)["limit"])
    return out


# ---------------------------------------------------------------------------
# Paired probes. A probe only RECORDS: it forks the controller at a shared
# state, drives two branches that differ on one axis, and returns a live
# envelope (diptych.live). The verdict comes from the requirement predicate in
# diptych.pilot.predicates, applied by the shared grader, so every verdict can
# be recomputed from the saved envelope.
# ---------------------------------------------------------------------------

Recorder = Callable[[Counted, dict, float, dict], dict]


def _envelope(op: str, coupling: str, ctx: dict, twins: list[dict], **meta: Any) -> dict:
    return live_envelope(
        task=TASK, requirement=REQUIREMENT[op], operator=op, coupling=coupling,
        artifact=ctx.get("artifact", ""),
        probe_id=f"live.{TASK}.{op.lower()}.s{ctx.get('seed', 0)}.t{ctx.get('t_p', 0)}",
        twins=twins, min_limit=CONFIG["min_limit"], max_limit=CONFIG["max_limit"],
        fork_tick=ctx.get("t_p"), **meta,
    )


def record_signflip(c: Counted, base: dict, last: float, ctx: dict) -> dict:
    sp, seed = base["setpoint_ms"], ctx["seed"]
    a, b = c.fork(), c.fork()
    hi, lo = sp * 1.2, sp * 0.8
    return _envelope("SIGNFLIP", "open_loop", ctx, [
        twin("deviation_up", {"latency": [hi], "limit": [last] + _feed(a, base, [hi])}, seed, deviation=0.2),
        twin("deviation_down", {"latency": [lo], "limit": [last] + _feed(b, base, [lo])}, seed, deviation=-0.2),
    ])


def record_trajswap(c: Counted, base: dict, last: float, ctx: dict) -> dict:
    sp, seed = base["setpoint_ms"], ctx["seed"]
    improving = [sp * (1.30 - 0.05 * i) for i in range(8)]
    worsening = list(reversed(improving))
    a, b = c.fork(), c.fork()
    return _envelope("TRAJSWAP", "open_loop", ctx, [
        twin("improving", {"latency": improving, "limit": _feed(a, base, improving)}, seed, order="improving"),
        twin("worsening", {"latency": worsening, "limit": _feed(b, base, worsening)}, seed, order="worsening"),
    ])


def record_satextend(c: Counted, base: dict, last: float, ctx: dict) -> dict:
    sp, seed = base["setpoint_ms"], ctx["seed"]
    hi = CONFIG["max_limit"]
    search = _feed_until(c.fork(), base, sp * 0.3, 200, hi - BOUND_EPS)
    k0 = len(search) if search and search[-1] >= hi - BOUND_EPS else None
    if k0 is None:  # never saturates: record the search so the predicate can say so
        top = max(search) if search else last
        return _envelope("SATEXTEND", "open_loop", ctx, [
            twin("short", {"release": [top, top]}, seed, saturated_ticks=0),
            twin("long", {"release": [top, top]}, seed, saturated_ticks=0),
        ], search_max_limit=top, k0=None)
    a, b = c.fork(), c.fork()
    # Short arm is barely saturated; long arm saturates 160 ticks longer. A
    # wound-up integrator responds late on the long arm only.
    la = _feed(a, base, [sp * 0.3] * (k0 + SAT_SHORT))
    lb = _feed(b, base, [sp * 0.3] * (k0 + SAT_LONG))
    ra = _feed(a, dict(base, t=base["t"] + k0 + SAT_SHORT), [sp * 1.3])[0]
    rb = _feed(b, dict(base, t=base["t"] + k0 + SAT_LONG), [sp * 1.3])[0]
    return _envelope("SATEXTEND", "open_loop", ctx, [
        twin("short", {"release": [la[-1], ra]}, seed, saturated_ticks=k0 + SAT_SHORT),
        twin("long", {"release": [lb[-1], rb]}, seed, saturated_ticks=k0 + SAT_LONG),
    ], search_max_limit=hi, k0=k0)


def _feed_until(ctrl: Counted, base: dict, latency: float, max_ticks: int, target: float) -> list[float]:
    """Feed a constant latency until the limit reaches ``target`` (or give up)."""
    out: list[float] = []
    t = int(base["t"])
    for _ in range(max_ticks):
        t += 1
        out.append(ctrl.tick(dict(base, t=t, latency_ms=latency))["limit"])
        if out[-1] >= target:
            break
    return out


def record_varscale(c: Counted, base: dict, last: float, ctx: dict) -> dict:
    t_p, seed = int(base["t"]) + 1, ctx["seed"]
    stop = t_p + 150
    # Same seed => identical per-tick draws (CRN); only the variance scale differs.
    stream = f"steady_long:{seed}"
    twins = []
    for name, scale in (("low_variance", 1.0), ("high_variance", 3.0)):
        trace: list[dict] = []
        run_closed_loop(c.fork(), Plant(SCENARIOS["steady_long"], seed=seed, var_scale=scale),
                        t_p, stop, last, trace)
        twins.append(twin(name, {"limit": [r["limit"] for r in trace]}, seed,
                          crn_stream_id=stream, crn_closed_loop=True, var_scale=scale))
    return _envelope("VARSCALE", "crn_closed_loop", ctx, twins)


def record_histswap(c: Counted, base: dict, last: float, ctx: dict) -> dict:
    sp, seed = base["setpoint_ms"], ctx["seed"]
    new_sp = sp * 0.7
    post = [new_sp * 1.1] * 6
    base2 = dict(base, t=base["t"] + 15)
    twins = []
    for name, level in (("history_high", 1.1), ("history_low", 0.9)):
        arm = c.fork()
        pre = _feed(arm, base, [sp * level] * 15)
        twins.append(twin(name, {"limit": pre[-1:] + _feed(arm, base2, post, setpoint=new_sp)}, seed,
                          pre_step_level=level))
    return _envelope("HISTSWAP", "open_loop", ctx, twins, new_setpoint_ms=new_sp)


RECORDERS: dict[str, Recorder] = {
    "SIGNFLIP": record_signflip,
    "TRAJSWAP": record_trajswap,
    "SATEXTEND": record_satextend,
    "VARSCALE": record_varscale,
    "HISTSWAP": record_histswap,
}
# Name kept for callers that iterate the in-process paired operators.
PAIRED_PROBES = RECORDERS
BRANCHES = {"SATEXTEND": 3}  # saturation search + two arms


def _combine(results: list[tuple[str, dict]]) -> str:
    verdicts = [v for v, _ in results]
    if "error" in verdicts:
        return "error"
    if "fail" in verdicts:
        return "fail"
    if "pass" in verdicts:
        return "pass"
    return "inconclusive"


def _save(envelope: dict, ctx: dict) -> None:
    out = os.environ.get("PILOT_ENVELOPE_DIR")
    if out:
        d = Path(out) / Path(str(ctx.get("artifact") or "artifact")).with_suffix("").name
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{envelope['probe_id']}.json").write_text(json.dumps(envelope, sort_keys=True))


def _grade(envelope: dict, ctx: dict) -> tuple[str, dict]:
    _save(envelope, ctx)
    res = grade_document(envelope)
    ev = dict(res.evidence)
    if res.actual_verdict == "inconclusive":
        ev.setdefault("reason", res.reason)
    return res.actual_verdict, ev


def run_forked_probes(cls: type, seed: int = 0, artifact: str = "") -> tuple[dict[str, Any], dict[str, int]]:
    """Run the in-process paired probes; return per-operator results and tick counts."""
    plant = Plant(SCENARIOS["steady"], seed=seed)
    counter = [0]
    root = Counted(cls(dict(CONFIG)), counter)
    prefix_trace: list[dict] = []
    prev = CONFIG["initial_limit"]
    naive = 0
    forks: list[tuple[int, Counted, dict, float]] = []
    t = 0
    for t_p in FORK_POINTS:
        prev = run_closed_loop(root, plant, t, t_p, prev, prefix_trace)
        t = t_p
        forks.append((t_p, root.fork(), prefix_trace[-1]["obs"], prev))
    prefix_ticks = counter[0]
    out: dict[str, Any] = {}
    for op, record in RECORDERS.items():
        per_fork = []
        for t_p, snap, base, last in forks:
            before = counter[0]
            ctx = {"seed": seed, "t_p": t_p, "artifact": artifact}
            try:
                v, ev = _grade(record(snap, base, last, ctx), ctx)
            except ArtifactError as exc:
                v, ev = "error", {"reason": str(exc)}
            suffix = counter[0] - before
            # Without prefix sharing every branch would replay the t_p-tick
            # prefix from scratch; count the branches this probe executed.
            naive += BRANCHES.get(op, 2) * t_p + suffix
            per_fork.append({"t_p": t_p, "verdict": v, "evidence": ev})
        out[op] = {"verdict": _combine([(r["verdict"], {}) for r in per_fork]), "forks": per_fork}
    ticks = {"forked": counter[0], "prefix_shared": prefix_ticks, "naive_equivalent": naive}
    out["_fork_fidelity"] = fork_fidelity(cls, seed, forks[0])
    return out, ticks


def fork_fidelity(cls: type, seed: int, fork: tuple[int, "Counted", dict, float]) -> dict[str, Any]:
    """Check deepcopy forking against replaying the prefix from scratch.

    A controller whose state lives outside the instance (module globals,
    class attributes mutated at run time) would make forked branches share
    state; replay is the ground truth.
    """
    t_p, snap, base, _last = fork
    sp = base["setpoint_ms"]
    forked = [_feed(snap.fork(), base, [sp * 1.2])[0], _feed(snap.fork(), base, [sp * 0.8])[0]]
    replayed = []
    for lat in (sp * 1.2, sp * 0.8):
        fresh = Counted(cls(dict(CONFIG)), [0])
        run_closed_loop(fresh, Plant(SCENARIOS["steady"], seed=seed), 0, t_p, CONFIG["initial_limit"])
        replayed.append(_feed(fresh, base, [lat])[0])
    return {"t_p": t_p, "forked": forked, "replayed": replayed, "ok": forked == replayed}


# ---------------------------------------------------------------------------
# Cross-process probes (RESEED, FREEZEDRY) and trace-set probe (SCHEMAX).
# ---------------------------------------------------------------------------

def closed_loop_trace(cls: type, scenario: str, seed: int = 0) -> list[dict]:
    plant = Plant(SCENARIOS[scenario], seed=seed)
    trace: list[dict] = []
    run_closed_loop(Counted(cls(dict(CONFIG)), [0]), plant, 0, plant.scenario.length,
                    CONFIG["initial_limit"], trace)
    return trace


def _decisions(trace: list[dict]) -> str:
    return json.dumps([[r["limit"], r["telemetry"]] for r in trace], sort_keys=True, default=repr)


def _worker(artifact: str, mode: str, env_extra: dict[str, str], stdin: str = "", timeout: int = 120) -> dict:
    env = dict(os.environ, **env_extra)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2])
    proc = subprocess.run(
        [sys.executable, "-m", "diptych.pilot.worker", "--artifact", artifact, "--mode", mode],
        input=stdin, capture_output=True, text=True, env=env, timeout=timeout, check=False,
    )
    if proc.returncode != 0:
        raise ArtifactError(f"worker {mode} failed: {proc.stderr.strip()[-300:]}")
    return json.loads(proc.stdout)


def _decision_twin(name: str, decisions: list, **meta: Any) -> dict:
    """Twin from a decoded decision sequence ``[[limit, telemetry], ...]``."""
    blob = json.dumps(decisions, sort_keys=True)
    return twin(name, {"limit": [d[0] for d in decisions]}, 0,
                decision_digest="sha256:" + hashlib.sha256(blob.encode()).hexdigest(), **meta)


def probe_reseed(artifact: str) -> dict:
    a = _worker(artifact, "trace", {"PYTHONHASHSEED": "1", "PILOT_GLOBAL_SEED": "11"})
    b = _worker(artifact, "trace", {"PYTHONHASHSEED": "2", "PILOT_GLOBAL_SEED": "22"})
    ctx = {"artifact": artifact}
    env = _envelope("RESEED", "open_loop", ctx, [
        _decision_twin("process_1", json.loads(a["decisions"]), hash_seed=1, global_seed=11),
        _decision_twin("process_2", json.loads(b["decisions"]), hash_seed=2, global_seed=22),
    ])
    v, ev = _grade(env, ctx)
    return {"verdict": v, "evidence": ev}


def probe_freezedry(artifact: str) -> dict:
    full = _worker(artifact, "trace", {"PYTHONHASHSEED": "0"})
    snap = _worker(artifact, "snapshot", {"PYTHONHASHSEED": "0"})
    resumed = _worker(artifact, "resume", {"PYTHONHASHSEED": "0"}, stdin=json.dumps(snap))
    ctx = {"artifact": artifact}
    env = _envelope("FREEZEDRY", "open_loop", ctx, [
        _decision_twin("uninterrupted", json.loads(full["decisions"])[snap["at"]:]),
        _decision_twin("restored", json.loads(resumed["decisions"])),
    ], restored_at=snap["at"])
    v, ev = _grade(env, ctx)
    ev["at"] = snap["at"]
    return {"verdict": v, "evidence": ev}


def probe_schemax(cls: type, artifact: str = "") -> dict:
    twins = []
    for sc in ("steady", "saturate", "setpoint_step"):
        keysets = sorted({"|".join(sorted(map(str, r["telemetry"]))) for r in closed_loop_trace(cls, sc)})
        twins.append(twin(sc, {"schema": keysets}, 0, scenario=sc))
    ctx = {"artifact": artifact}
    v, ev = _grade(_envelope("SCHEMAX", "open_loop", ctx, twins), ctx)
    return {"verdict": v, "evidence": ev}


# ---------------------------------------------------------------------------
# Single-trace grader: one closed-loop run per check, as the prose reads.
# ---------------------------------------------------------------------------

def _deltas(trace: list[dict]) -> list[float]:
    lims = [CONFIG["initial_limit"]] + [r["limit"] for r in trace]
    return [b - a for a, b in zip(lims, lims[1:])]


def _err(r: dict) -> float:
    o = r["obs"]
    return (o["latency_ms"] - o["setpoint_ms"]) / o["setpoint_ms"]


def single_trace_grade(cls: type) -> dict[str, str]:
    out: dict[str, str] = {}
    steady = closed_loop_trace(cls, "steady")
    d = _deltas(steady)
    downs = [-x for x, r in zip(d, steady) if _err(r) > 0.02]
    ups = [x for x, r in zip(d, steady) if _err(r) < -0.02]
    if downs and ups:
        md, mu = sum(downs) / len(downs), sum(ups) / len(ups)
        out["SIGNFLIP"] = "pass" if md > 0 and md > 2 * mu else "fail"
    else:
        out["SIGNFLIP"] = "pass"

    lat = [r["obs"]["latency_ms"] for r in steady]
    improving = [i for i in range(2, len(steady)) if lat[i - 2] > lat[i - 1] > lat[i]]
    if improving:
        frac = sum(1 for i in improving if d[i] > 0) / len(improving)
        out["TRAJSWAP"] = "pass" if frac >= 0.5 else "fail"
    else:
        out["TRAJSWAP"] = "pass"

    sat = closed_loop_trace(cls, "saturate")
    hi = CONFIG["max_limit"] - BOUND_EPS
    saturated = [i for i, r in enumerate(sat) if r["limit"] >= hi]
    verdict = "pass"
    if saturated:
        after = [i for i in range(saturated[0], len(sat)) if _err(sat[i]) > 0]
        if after:
            i0 = after[0]
            verdict = "pass" if any(r["limit"] < hi for r in sat[i0:i0 + 3]) else "fail"
    out["SATEXTEND"] = verdict

    vol = closed_loop_trace(cls, "volatile_step")
    early = sum(r["limit"] for r in vol[60:120]) / 60
    late = sum(r["limit"] for r in vol[180:240]) / 60
    out["VARSCALE"] = "pass" if late < early else "fail"

    step = closed_loop_trace(cls, "setpoint_step")
    # "Quiet" read as no hunting after the step: large corrections (>= 2
    # limit units, above noise jitter) must not keep reversing direction.
    big = [x for x in _deltas(step)[120:140] if abs(x) >= 2.0]
    flips = sum(1 for a, b in zip(big, big[1:]) if a * b < 0)
    out["HISTSWAP"] = "pass" if flips <= 2 else "fail"

    again = closed_loop_trace(cls, "steady")
    out["RESEED"] = "pass" if _decisions(again) == _decisions(steady) else "fail"

    c = cls(dict(CONFIG))
    plant = Plant(SCENARIOS["steady"])
    run_closed_loop(Counted(c, [0]), plant, 0, 240, CONFIG["initial_limit"])
    blob = c.snapshot()
    out["FREEZEDRY"] = "pass" if cls.restore(blob).snapshot() == blob else "fail"

    keys = {tuple(sorted(map(str, r["telemetry"]))) for r in steady}
    out["SCHEMAX"] = "pass" if len(keys) == 1 else "fail"
    return out


# ---------------------------------------------------------------------------
# Strong single-trace baseline: several readings per requirement, each applied
# to every scenario where it is meaningful; a requirement fails if ANY reading
# on ANY single trace flags it. Readings that false-alarm on the conforming
# control are excluded (STRONG_EXCLUDED), mirroring how paired probes are
# calibrated. This gives single-trace grading every advantage we could think of.
# ---------------------------------------------------------------------------

def _ls_gain(pairs: list[tuple[float, float]]) -> float | None:
    """Least-squares k in delta = -k * e over (e, delta) pairs."""
    den = sum(e * e for e, _ in pairs)
    if den <= 0:
        return None
    return -sum(e * dl for e, dl in pairs) / den


def _read_sign_means(tr: list[dict]) -> bool:
    d = _deltas(tr)
    downs = [-x for x, r in zip(d, tr) if _err(r) > 0.02]
    ups = [x for x, r in zip(d, tr) if _err(r) < -0.02]
    if not downs or not ups:
        return True
    md, mu = sum(downs) / len(downs), sum(ups) / len(ups)
    return md > 0 and md > 2 * mu


def _read_sign_slopes(tr: list[dict]) -> bool:
    d = _deltas(tr)
    pos = [(_err(r), x) for x, r in zip(d, tr) if _err(r) > 0.02]
    neg = [(_err(r), x) for x, r in zip(d, tr) if _err(r) < -0.02]
    kd, ku = _ls_gain(pos), _ls_gain(neg)
    if kd is None or ku is None:
        return True
    return kd > 0 and kd > 2 * ku


def _read_trend_frac(tr: list[dict]) -> bool:
    d = _deltas(tr)
    lat = [r["obs"]["latency_ms"] for r in tr]
    imp = [i for i in range(2, len(tr)) if lat[i - 2] > lat[i - 1] > lat[i]]
    if not imp:
        return True
    return sum(1 for i in imp if d[i] > 0) / len(imp) >= 0.5


def _read_trend_regression(tr: list[dict]) -> bool:
    """Regress delta_t on [e_t, e_t - e_{t-3}]; worsening trend must tighten."""
    d = _deltas(tr)
    e = [_err(r) for r in tr]
    rows = [(e[t], e[t] - e[t - 3], d[t]) for t in range(3, len(tr))]
    if len(rows) < 10:
        return True
    sxx = sum(a * a for a, _, _ in rows); syy = sum(b * b for _, b, _ in rows)
    sxy = sum(a * b for a, b, _ in rows)
    sxd = sum(a * dl for a, _, dl in rows); syd = sum(b * dl for _, b, dl in rows)
    det = sxx * syy - sxy * sxy
    if abs(det) < 1e-12:
        return True
    beta_trend = (sxx * syd - sxy * sxd) / det
    return beta_trend < 0


def _read_vol_halves(tr: list[dict]) -> bool:
    early = sum(r["limit"] for r in tr[60:120]) / 60
    late = sum(r["limit"] for r in tr[180:240]) / 60
    return late < early


def _read_vol_correlation(tr: list[dict]) -> bool:
    """Rolling spread of error vs. limit must correlate negatively."""
    e = [_err(r) for r in tr]
    xs, ys = [], []
    for t in range(20, len(tr)):
        w = e[t - 20:t]
        m = sum(w) / 20
        xs.append((sum((v - m) ** 2 for v in w) / 20) ** 0.5)
        ys.append(tr[t]["limit"])
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return cov < 0


def _read_sat_leaves(tr: list[dict]) -> bool:
    hi = CONFIG["max_limit"] - BOUND_EPS
    sat = [i for i, r in enumerate(tr) if r["limit"] >= hi]
    if not sat:
        return True
    after = [i for i in range(sat[0], len(tr)) if _err(tr[i]) > 0]
    return not after or any(r["limit"] < hi for r in tr[after[0]:after[0] + 3])


def _read_sat_immediate(tr: list[dict]) -> bool:
    hi = CONFIG["max_limit"] - BOUND_EPS
    sat = [i for i, r in enumerate(tr) if r["limit"] >= hi]
    if not sat:
        return True
    after = [i for i in range(sat[0], len(tr)) if _err(tr[i]) > 0]
    return not after or tr[after[0]]["limit"] < hi


def _read_quiet_flips(tr: list[dict]) -> bool:
    big = [x for x in _deltas(tr)[120:140] if abs(x) >= 2.0]
    return sum(1 for a, b in zip(big, big[1:]) if a * b < 0) <= 2


def _read_quiet_settles(tr: list[dict]) -> bool:
    d = [abs(x) for x in _deltas(tr)]
    return sum(d[132:140]) / 8 <= sum(d[120:128]) / 8 + 1e-9


def _read_quiet_undershoot(tr: list[dict]) -> bool:
    later = sum(r["limit"] for r in tr[140:160]) / 20
    return min(r["limit"] for r in tr[120:140]) >= 0.8 * later


def _read_schema(tr: list[dict]) -> bool:
    return len({tuple(sorted(map(str, r["telemetry"]))) for r in tr}) == 1


ALL_SCENARIOS = ("steady", "volatile_step", "setpoint_step", "saturate")
STRONG_READINGS: dict[str, list[tuple[str, Callable[[list[dict]], bool], tuple[str, ...]]]] = {
    "SIGNFLIP": [("mean-ratio", _read_sign_means, ALL_SCENARIOS),
                 ("slope-ratio", _read_sign_slopes, ALL_SCENARIOS)],
    "TRAJSWAP": [("improving-frac", _read_trend_frac, ("steady", "volatile_step", "setpoint_step")),
                 ("trend-regression", _read_trend_regression, ("steady", "volatile_step", "setpoint_step"))],
    "VARSCALE": [("halves", _read_vol_halves, ("volatile_step",)),
                 ("spread-correlation", _read_vol_correlation, ("volatile_step",))],
    "SATEXTEND": [("leaves-within-3", _read_sat_leaves, ("saturate",)),
                  ("immediate-drop", _read_sat_immediate, ("saturate",))],
    "HISTSWAP": [("no-hunting", _read_quiet_flips, ("setpoint_step",)),
                 ("settles", _read_quiet_settles, ("setpoint_step",)),
                 ("no-undershoot", _read_quiet_undershoot, ("setpoint_step",))],
    "SCHEMAX": [("stable-keys", _read_schema, ALL_SCENARIOS)],
}
# Filled by calibrate_strong(): (operator, reading, scenario) triples that
# flag the conforming control and are therefore not used.
STRONG_EXCLUDED: set[tuple[str, str, str]] = set()


def _strong_cells(cls: type) -> dict[tuple[str, str, str], bool]:
    traces = {sc: closed_loop_trace(cls, sc) for sc in ALL_SCENARIOS}
    out = {}
    for op, readings in STRONG_READINGS.items():
        for name, fn, scs in readings:
            for sc in scs:
                out[(op, name, sc)] = fn(traces[sc])
    return out


def calibrate_strong() -> set[tuple[str, str, str]]:
    """Exclude readings that false-alarm on the conforming control."""
    here = Path(__file__).resolve().parent
    conf = load_controller_class(here / "controls" / "conforming.py")
    bad = {k for k, ok in _strong_cells(conf).items() if not ok}
    STRONG_EXCLUDED.clear()
    STRONG_EXCLUDED.update(bad)
    return bad


def single_trace_grade_strong(cls: type) -> dict[str, str]:
    """Union of all calibrated single-trace readings; RESEED/FREEZEDRY as in the basic grader."""
    if not STRONG_EXCLUDED and not getattr(single_trace_grade_strong, "_calibrated", False):
        calibrate_strong()
        single_trace_grade_strong._calibrated = True  # type: ignore[attr-defined]
    cells = _strong_cells(cls)
    basic = single_trace_grade(cls)
    out = {}
    for op in OPERATORS:
        if op in STRONG_READINGS:
            flagged = [k for k, ok in cells.items() if k[0] == op and k not in STRONG_EXCLUDED and not ok]
            out[op] = "fail" if flagged else "pass"
        else:
            out[op] = basic[op]
    return out


def objective(cls: type) -> dict[str, float]:
    """Task-performance metrics a trace grader would report (steady scenario)."""
    tr = closed_loop_trace(cls, "steady")
    errs = [abs(_err(r)) for r in tr[40:]]
    return {
        "mean_abs_latency_error": sum(errs) / len(errs),
        "mean_admitted_rps": sum(r["obs"]["admitted_rps"] for r in tr[40:]) / len(errs),
    }


SEEDS = (0, 1, 2)


def evaluate(artifact: str, seeds: tuple[int, ...] = SEEDS) -> dict[str, Any]:
    """Full pilot evaluation of one artifact file.

    Forked probes run at every seed in ``seeds`` (different plant noise and
    fork states); like fork points, a violation witnessed at any seed fails
    the requirement. Per-seed verdicts are kept for stability reporting.
    """
    result: dict[str, Any] = {"artifact": artifact, "paired": {}, "single_trace": {}, "errors": []}
    try:
        cls = load_controller_class(artifact)
    except Exception as exc:  # noqa: BLE001
        result["load_error"] = f"{type(exc).__name__}: {exc}"
        return result
    try:
        per_seed: dict[int, dict[str, Any]] = {}
        ticks = {"forked": 0, "prefix_shared": 0, "naive_equivalent": 0}
        fidelity = []
        for seed in seeds:
            forked, t = run_forked_probes(cls, seed, artifact)
            fidelity.append(forked.pop("_fork_fidelity"))
            per_seed[seed] = forked
            for k in ticks:
                ticks[k] += t[k]
        result["fork_fidelity"] = {"ok": all(f["ok"] for f in fidelity), "checks": fidelity}
        result["paired_by_seed"] = {str(sd): {op: r["verdict"] for op, r in f.items()}
                                    for sd, f in per_seed.items()}
        result["paired"].update({
            op: _combine([(per_seed[sd][op]["verdict"], {}) for sd in seeds]) for op in PAIRED_PROBES
        })
        result["paired_evidence"] = per_seed[seeds[0]]
        result["ticks"] = ticks
    except ArtifactError as exc:
        result["errors"].append(f"forked: {exc}")
        result["paired"].update({op: "error" for op in PAIRED_PROBES})
    for op, fn in (("RESEED", lambda: probe_reseed(artifact)),
                   ("FREEZEDRY", lambda: probe_freezedry(artifact)),
                   ("SCHEMAX", lambda: probe_schemax(cls, artifact))):
        try:
            result["paired"][op] = fn()["verdict"]
        except (ArtifactError, subprocess.TimeoutExpired, Exception) as exc:  # noqa: BLE001
            result["paired"][op] = "error"
            result["errors"].append(f"{op}: {exc}")
    try:
        result["single_trace"] = single_trace_grade(cls)
    except Exception as exc:  # noqa: BLE001
        result["errors"].append(f"single_trace: {type(exc).__name__}: {exc}")
        result["single_trace"] = {op: "error" for op in OPERATORS}
    try:
        result["single_trace_strong"] = single_trace_grade_strong(cls)
        result["strong_excluded"] = sorted("/".join(k) for k in STRONG_EXCLUDED)
    except Exception as exc:  # noqa: BLE001
        result["errors"].append(f"single_trace_strong: {type(exc).__name__}: {exc}")
        result["single_trace_strong"] = {op: "error" for op in OPERATORS}
    try:
        result["objective"] = objective(cls)
    except Exception as exc:  # noqa: BLE001
        result["errors"].append(f"objective: {type(exc).__name__}: {exc}")
    result["paired"] = {op: result["paired"].get(op, "error") for op in OPERATORS}
    result["single_trace"] = {op: result["single_trace"].get(op, "error") for op in OPERATORS}
    result["single_trace_strong"] = {op: result.get("single_trace_strong", {}).get(op, "error") for op in OPERATORS}
    return result
