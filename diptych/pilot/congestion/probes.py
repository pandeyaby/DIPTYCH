"""Paired probes, single-run checks and baselines for the congestion task.

Same structure as ``diptych.pilot.probes``: a probe RECORDS a pair of
executions into a live envelope (``diptych.live``); the requirement predicate
(``predicates.py``) grades the envelope through the shared grader. The three
requirements that RFC 9743 states as single-run properties are graded by
single-run checks. Every candidate ``tick`` is counted.
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
from diptych.pilot.congestion.plant import BUFFER, CAPACITY, CONFIG, Flow, Path, Sim
from diptych.pilot.congestion.predicates import PAIRED, SINGLE_RUN, TASK
from diptych.pilot.congestion.reference import Controller as Reno

KEYS: tuple[str, ...] = SINGLE_RUN + PAIRED
SEEDS = (0, 1, 2)
FORK_POINTS = (250, 350)
JOIN_AT = 100          # competing flow joins an established standard flow
END = 700
SHORT_SIZE = 150.0     # packets
SHORT_WINDOW = 300     # ticks recorded for the short flow


class ArtifactError(Exception):
    """The artifact raised or returned an ill-formed decision."""


def load_controller_class(path: str | FsPath) -> type:
    path = FsPath(path)
    spec = importlib.util.spec_from_file_location(f"cc_artifact_{abs(hash(str(path)))}", path)
    if spec is None or spec.loader is None:
        raise ArtifactError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    cls = getattr(mod, "Controller", None)
    if cls is None:
        raise ArtifactError("module defines no Controller")
    return cls


class Counted:
    """Wrap a candidate controller; count and validate every tick."""

    def __init__(self, ctrl: Any, counter: list[int]):
        self.ctrl = ctrl
        self.counter = counter

    def tick(self, obs: dict) -> dict:
        self.counter[0] += 1
        try:
            d = self.ctrl.tick(dict(obs))
        except Exception as exc:  # noqa: BLE001
            raise ArtifactError(f"tick raised {type(exc).__name__}: {exc}") from exc
        if not isinstance(d, dict) or "cwnd" not in d:
            raise ArtifactError("decision is not a dict with 'cwnd'")
        w = d["cwnd"]
        if not isinstance(w, (int, float)) or isinstance(w, bool) or not math.isfinite(w):
            raise ArtifactError(f"cwnd not a finite number: {w!r}")
        tel = d.get("telemetry", {})
        if not isinstance(tel, dict):
            raise ArtifactError("telemetry is not a dict")
        return {"cwnd": float(w), "telemetry": tel}

    def __deepcopy__(self, memo: dict) -> "Counted":
        try:
            return Counted(copy.deepcopy(self.ctrl, memo), self.counter)  # counter is shared
        except Exception as exc:  # noqa: BLE001
            raise ArtifactError(f"state not copyable: {exc}") from exc


def _cand(cls: type, counter: list[int]) -> Counted:
    try:
        return Counted(cls(dict(CONFIG)), counter)
    except Exception as exc:  # noqa: BLE001
        raise ArtifactError(f"constructor raised {type(exc).__name__}: {exc}") from exc


def _reno() -> Reno:
    return Reno(dict(CONFIG))


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _series(flow: Flow, key: str, start: int, stop: int) -> list[float]:
    return [r[key] for r in flow.log if start <= r["t"] < stop]


# ---------------------------------------------------------------------------
# Paired probes (recorders)
# ---------------------------------------------------------------------------

def _envelope(req: str, op: str, coupling: str, ctx: dict, twins: list[dict], **meta: Any) -> dict:
    return live_envelope(
        task=TASK, requirement=req, operator=op, coupling=coupling,
        artifact=ctx.get("artifact", ""),
        probe_id=f"live.{TASK}.{req}.s{ctx['seed']}.t{ctx.get('t_p', 0)}",
        twins=twins, capacity=CAPACITY, buffer=BUFFER, fork_tick=ctx.get("t_p"), **meta,
    )


def record_harm(cls: type, counter: list[int], ctx: dict) -> list[dict]:
    """REFSWAP: an established standard flow is joined by the candidate (a) or
    by another standard flow (b). One pair, two requirements."""
    seed = ctx["seed"]
    stream = f"bottleneck:{seed}"
    prefix = Sim([Flow(_reno())], seed=seed).run(JOIN_AT)
    twins_thr, twins_q = [], []
    for name, joiner in (("vs_candidate", _cand(cls, counter)), ("vs_standard", _reno())):
        sim = copy.deepcopy(prefix)
        sim.flows.append(Flow(joiner, start=JOIN_AT))
        sim.run(END)
        meta = {"crn_stream_id": stream, "crn_closed_loop": True, "competitor": name}
        twins_thr.append(twin(name, {"victim_throughput": _series(sim.flows[0], "acked", 300, END)}, seed, **meta))
        twins_q.append(twin(name, {"queue": sim.queue_log[300:END]}, seed, **meta))
    c = dict(ctx, t_p=JOIN_AT)
    return [_envelope("harm-throughput", "REFSWAP", "crn_closed_loop", c, twins_thr),
            _envelope("harm-latency", "REFSWAP", "crn_closed_loop", c, twins_q)]


def record_short_flows(solo: Sim, ctx: dict) -> dict:
    """REFSWAP: a short standard flow starts beside the established candidate
    (a) or beside an established standard flow (b)."""
    seed, t_p = ctx["seed"], ctx["t_p"]
    stream = f"bottleneck:{seed}"
    ref = Sim([Flow(_reno())], seed=seed).run(t_p)
    twins = []
    for name, base in (("beside_candidate", solo), ("beside_standard", ref)):
        sim = copy.deepcopy(base)
        short = Flow(_reno(), start=t_p, size=SHORT_SIZE)
        sim.flows.append(short)
        sim.run(t_p + SHORT_WINDOW)
        cum, total = [], 0.0
        for r in short.log:
            total += r["acked"]
            cum.append(total)
        cum += [total] * (SHORT_WINDOW - len(cum))
        done = None if short.done_at is None else short.done_at - t_p + 1
        twins.append(twin(name, {"short_delivered": cum}, seed, crn_stream_id=stream,
                          crn_closed_loop=True, completion_ticks=done))
    return _envelope("short-flows", "REFSWAP", "crn_closed_loop", ctx, twins, short_size=SHORT_SIZE)


def record_delay_change(solo: Sim, ctx: dict) -> dict:
    """PATHSTEP: from the fork, base RTT stays (a) or doubles (b); capacity fixed."""
    seed, t_p = ctx["seed"], ctx["t_p"]
    stream = f"bottleneck:{seed}"
    twins = []
    for name, factor in (("unchanged", 1.0), ("delay_doubled", 2.0)):
        sim = copy.deepcopy(solo)
        sim.path.base_rtt *= factor
        sim.run(t_p + 300)
        twins.append(twin(name, {"throughput": _series(sim.flows[0], "acked", t_p, t_p + 300)}, seed,
                          crn_stream_id=stream, crn_closed_loop=True, rtt_factor=factor))
    return _envelope("delay-change", "PATHSTEP", "crn_closed_loop", ctx, twins)


def record_path_change(cls: type, counter: list[int], solo: Sim, ctx: dict) -> dict:
    """HISTSWAP: capacity halves at the fork (a) vs. a run on a path that
    always had the lower capacity (b). Same present, different history."""
    seed, t_p = ctx["seed"], ctx["t_p"]
    stream = f"bottleneck:{seed}"
    changed = copy.deepcopy(solo)
    changed.path.capacity *= 0.5
    changed.run(t_p + 300)
    always = Sim([Flow(_cand(cls, counter))], path=Path(capacity=CAPACITY * 0.5), seed=seed).run(t_p + 300)
    twins = []
    for name, sim in (("changed", changed), ("always_low", always)):
        w0, w1 = t_p + 150, t_p + 300
        twins.append(twin(name, {
            "throughput": _series(sim.flows[0], "acked", w0, w1),
            "lost": _series(sim.flows[0], "lost", w0, w1),
            "queue": sim.queue_log[w0:w1],
        }, seed, crn_stream_id=stream, crn_closed_loop=True, history=name))
    return _envelope("path-change", "HISTSWAP", "crn_closed_loop", ctx, twins,
                     new_capacity=CAPACITY * 0.5)


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
    per: dict[str, list[dict]] = {k: [] for k in PAIRED}

    def add(req: str, envelope: dict, ctx: dict) -> None:
        v, ev = _grade(envelope, ctx)
        per[req].append({"t_p": ctx.get("t_p"), "verdict": v, "evidence": ev})

    ctx = {"seed": seed, "artifact": artifact}
    before = counter[0]
    try:
        for env in record_harm(cls, counter, ctx):
            add(env["requirement"], env, dict(ctx, t_p=JOIN_AT))
    except ArtifactError as exc:
        for req in ("harm-throughput", "harm-latency"):
            per[req].append({"t_p": JOIN_AT, "verdict": "error", "evidence": {"reason": str(exc)}})
    naive += counter[0] - before  # the candidate joins after the fork: nothing to share

    solo = Sim([Flow(_cand(cls, counter))], seed=seed)
    prefix_ticks = 0
    for t_p in FORK_POINTS:
        start = counter[0]
        try:
            solo.run(t_p)
        except ArtifactError as exc:
            for req in ("short-flows", "delay-change", "path-change"):
                per[req].append({"t_p": t_p, "verdict": "error", "evidence": {"reason": str(exc)}})
            continue
        prefix_ticks += counter[0] - start
        c = dict(ctx, t_p=t_p)
        for req, rec, branches in (
            ("short-flows", lambda: record_short_flows(solo, c), 1),
            ("delay-change", lambda: record_delay_change(solo, c), 2),
            ("path-change", lambda: record_path_change(cls, counter, solo, c), 1),
        ):
            before = counter[0]
            try:
                add(req, rec(), c)
            except ArtifactError as exc:
                per[req].append({"t_p": t_p, "verdict": "error", "evidence": {"reason": str(exc)}})
            # Without sharing, each branch forked from the solo run replays its prefix.
            naive += branches * t_p + (counter[0] - before)
    out = {req: {"verdict": _combine([r["verdict"] for r in rows]), "forks": rows} for req, rows in per.items()}
    return out, {"forked": counter[0], "prefix_shared": prefix_ticks, "naive_equivalent": naive}


def fork_fidelity(cls: type, seed: int) -> dict[str, Any]:
    """Forking the simulation must equal running it straight through."""
    counter = [0]
    t_p = FORK_POINTS[0]
    forked = copy.deepcopy(Sim([Flow(_cand(cls, counter))], seed=seed).run(t_p)).run(t_p + 100)
    replayed = Sim([Flow(_cand(cls, counter))], seed=seed).run(t_p + 100)
    a = _series(forked.flows[0], "cwnd", t_p, t_p + 100)
    b = _series(replayed.flows[0], "cwnd", t_p, t_p + 100)
    return {"t_p": t_p, "ok": a == b}


# ---------------------------------------------------------------------------
# Single-run requirements (trace properties in RFC 9743's own terms)
# ---------------------------------------------------------------------------

def _solo_run(cls: type, seed: int, events: dict[int, Callable[[Path], None]], until: int = 600) -> Sim:
    sim = Sim([Flow(_cand(cls, [0]))], seed=seed)
    for t in sorted(events) + [until]:
        sim.run(min(t, until))
        if t in events and t < until:
            events[t](sim.path)
    return sim


def check_full_backoff(cls: type, seed: int) -> tuple[str, dict]:
    sim = _solo_run(cls, seed, {300: lambda p: setattr(p, "blackout", True)}, until=360)
    worst = max(_series(sim.flows[0], "cwnd", 330, 360))
    return ("pass" if worst <= 2.0 + 1e-9 else "fail"), {"max_cwnd_in_blackout": worst}


def check_loss_reduction(cls: type, seed: int) -> tuple[str, dict]:
    sim = _solo_run(cls, seed, {300: lambda p: setattr(p, "loss_rate", 0.2)}, until=400)
    before = _mean(_series(sim.flows[0], "cwnd", 200, 300))
    during = _mean(_series(sim.flows[0], "cwnd", 350, 400))
    return ("pass" if during < 0.5 * before else "fail"), {"cwnd_before": before, "cwnd_under_loss": during}


def check_intra_fairness(cls: type, seed: int) -> tuple[str, dict]:
    sim = Sim([Flow(_cand(cls, [0])), Flow(_cand(cls, [0]), start=JOIN_AT)], seed=seed).run(END)
    a = _mean(_series(sim.flows[0], "acked", 400, END))
    b = _mean(_series(sim.flows[1], "acked", 400, END))
    jain = (a + b) ** 2 / (2 * (a * a + b * b)) if a + b > 0 else 0.0
    return ("pass" if jain >= 0.9 else "fail"), {"jain": jain, "throughput": [a, b]}


SINGLE_RUN_CHECKS: dict[str, Callable[[type, int], tuple[str, dict]]] = {
    "full-backoff": check_full_backoff,
    "loss-reduction": check_loss_reduction,
    "intra-fairness": check_intra_fairness,
}


# ---------------------------------------------------------------------------
# Single-trace baselines for the paired requirements: one run each.
# ---------------------------------------------------------------------------

def _competition(cls: type, seed: int) -> Sim:
    sim = Sim([Flow(_reno())], seed=seed).run(JOIN_AT)
    sim.flows.append(Flow(_cand(cls, [0]), start=JOIN_AT))
    return sim.run(END)


def _short_beside(cls: type, seed: int, t_p: int = 300) -> Flow:
    sim = Sim([Flow(_cand(cls, [0]))], seed=seed).run(t_p)
    short = Flow(_reno(), start=t_p, size=SHORT_SIZE)
    sim.flows.append(short)
    sim.run(t_p + SHORT_WINDOW)
    return short


# name -> (requirement, reading over one run). True = no violation flagged.
def _b_share(cls: type, seed: int) -> bool:
    sim = _competition(cls, seed)
    v = _mean(_series(sim.flows[0], "acked", 300, END))
    c = _mean(_series(sim.flows[1], "acked", 300, END))
    return v / (v + c) >= 0.4 if v + c > 0 else False


def _b_victim_abs(cls: type, seed: int) -> bool:
    sim = _competition(cls, seed)
    return _mean(_series(sim.flows[0], "acked", 300, END)) >= 0.4 * CAPACITY


def _b_jain(cls: type, seed: int) -> bool:
    sim = _competition(cls, seed)
    v = _mean(_series(sim.flows[0], "acked", 300, END))
    c = _mean(_series(sim.flows[1], "acked", 300, END))
    return (v + c) ** 2 / (2 * (v * v + c * c)) >= 0.9 if v + c > 0 else False


def _b_queue_mean(cls: type, seed: int) -> bool:
    return _mean(_competition(cls, seed).queue_log[300:END]) <= 0.75 * BUFFER


def _b_queue_p95(cls: type, seed: int) -> bool:
    q = sorted(_competition(cls, seed).queue_log[300:END])
    return q[int(0.95 * len(q))] <= 0.99 * BUFFER or _mean(q) <= 0.7 * BUFFER


def _b_victim_rtt(cls: type, seed: int) -> bool:
    sim = _competition(cls, seed)
    return _mean(_series(sim.flows[0], "rtt", 300, END)) <= 2.0 * 1.8  # base RTT 2 ticks


def _b_short_abs(cls: type, seed: int) -> bool:
    short = _short_beside(cls, seed)
    return short.done_at is not None and short.done_at - 300 + 1 <= 60


def _b_short_rate(cls: type, seed: int) -> bool:
    short = _short_beside(cls, seed)
    first = sum(r["acked"] for r in short.log[:30])
    return first >= 0.3 * CAPACITY * 30


def _b_delay_before_after(cls: type, seed: int) -> bool:
    sim = _solo_run(cls, seed, {300: lambda p: setattr(p, "base_rtt", p.base_rtt * 2)}, until=600)
    return _mean(_series(sim.flows[0], "acked", 450, 600)) >= 0.8 * _mean(_series(sim.flows[0], "acked", 150, 300))


def _b_delay_utilization(cls: type, seed: int) -> bool:
    sim = _solo_run(cls, seed, {300: lambda p: setattr(p, "base_rtt", p.base_rtt * 2)}, until=600)
    return _mean(_series(sim.flows[0], "acked", 450, 600)) >= 0.8 * CAPACITY


def _path_run(cls: type, seed: int) -> Sim:
    return _solo_run(cls, seed, {300: lambda p: setattr(p, "capacity", p.capacity * 0.5)}, until=600)


def _b_path_utilization(cls: type, seed: int) -> bool:
    sim = _path_run(cls, seed)
    return _mean(_series(sim.flows[0], "acked", 450, 600)) >= 0.8 * CAPACITY * 0.5


def _b_path_loss(cls: type, seed: int) -> bool:
    sim = _path_run(cls, seed)
    sent = sum(r["acked"] + r["lost"] for r in sim.flows[0].log if 450 <= r["t"] < 600)
    lost = sum(_series(sim.flows[0], "lost", 450, 600))
    return lost / sent <= 0.03 if sent > 0 else False


def _b_path_queue(cls: type, seed: int) -> bool:
    return _mean(_path_run(cls, seed).queue_log[450:600]) <= 0.9 * BUFFER


Reading = Callable[[type, int], bool]
BASIC: dict[str, Reading] = {
    "harm-throughput": _b_share,
    "harm-latency": _b_queue_mean,
    "short-flows": _b_short_abs,
    "delay-change": _b_delay_before_after,
    "path-change": _b_path_utilization,
}
STRONG: dict[str, list[tuple[str, Reading]]] = {
    "harm-throughput": [("share", _b_share), ("victim-absolute", _b_victim_abs), ("jain", _b_jain)],
    "harm-latency": [("queue-mean", _b_queue_mean), ("queue-p95", _b_queue_p95), ("victim-rtt", _b_victim_rtt)],
    "short-flows": [("completion", _b_short_abs), ("early-rate", _b_short_rate)],
    "delay-change": [("before-after", _b_delay_before_after), ("utilization", _b_delay_utilization)],
    "path-change": [("utilization", _b_path_utilization), ("loss", _b_path_loss), ("queue", _b_path_queue)],
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
    out = {}
    for req, check in SINGLE_RUN_CHECKS.items():
        out[req] = _combine([check(cls, s)[0] for s in SEEDS])
    return out


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


GOAL_LOSSY_THROUGHPUT = 0.7   # fraction of capacity under 1% random loss
GOAL_QUEUE = 0.4              # fraction of the buffer, alone on the link


def objective(cls: type) -> dict[str, Any]:
    """The task's two performance goals (not graded requirements)."""
    lossy = Sim([Flow(_cand(cls, [0]))], path=Path(loss_rate=0.01), seed=0).run(600)
    solo = Sim([Flow(_cand(cls, [0]))], seed=0).run(600)
    thr = _mean(_series(lossy.flows[0], "acked", 200, 600)) / CAPACITY
    queue = _mean(solo.queue_log[200:600]) / BUFFER
    return {"lossy_throughput_fraction": thr, "solo_queue_fraction": queue,
            "solo_throughput_fraction": _mean(_series(solo.flows[0], "acked", 200, 600)) / CAPACITY,
            "meets_lossy_goal": thr >= GOAL_LOSSY_THROUGHPUT, "meets_delay_goal": queue <= GOAL_QUEUE}


def evaluate(artifact: str, seeds: tuple[int, ...] = SEEDS) -> dict[str, Any]:
    """Full evaluation of one congestion-control artifact."""
    result: dict[str, Any] = {"artifact": artifact, "task": TASK, "paired": {}, "single_trace": {},
                              "single_trace_strong": {}, "errors": []}
    try:
        cls = load_controller_class(artifact)
    except Exception as exc:  # noqa: BLE001
        result["load_error"] = f"{type(exc).__name__}: {exc}"
        for k in ("paired", "single_trace", "single_trace_strong"):
            result[k] = {key: "error" for key in KEYS}
        return result
    # Single-run requirements: same check for every grader.
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
