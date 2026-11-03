"""RQ3 analysis: do paired verdicts predict held-out harm? (paper/RQ3_PREREG.md)

Unit: a (generated controller, behavioral requirement) cell with decided
verdicts (pass/fail) and a held-out outcome H (higher is worse). AUCs are
pooled across requirement strata: within each stratum, the probability that
a flagged cell has worse H than an unflagged one (ties count half), weighted
by the number of flagged x unflagged pairs.

H1 (primary): among cells the basic single-trace check passes, paired-fail
    cells have worse H. Pooled AUC > 0.5, one-sided stratified permutation
    test (labels permuted within requirement), alpha 0.05.
H2: pooled AUC of the paired verdict vs. the basic and strong single-trace
    verdicts over all cells; 95% CI of the difference by bootstrap over
    controllers.
H3: per controller, Spearman correlation of the number of paired failures
    with the composite held-out rank (mean within-requirement percentile of
    H), vs. the same for basic failures.

    python -m diptych.pilot.rq3      # reads results*.json and heldout*.json; writes rq3.json
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
TASKS = {"concurrency": ("results.json", "heldout.json"),
         "congestion": ("results_congestion.json", "heldout_congestion.json"),
         "autoscale": ("results_autoscale.json", "heldout_autoscale.json")}
DECIDED = ("pass", "fail")
GRADERS = ("paired", "single_trace", "single_trace_strong")
N_PERM = 10_000
N_BOOT = 2_000


def load_cells(task: str) -> list[dict[str, Any]]:
    res_f, ho_f = TASKS[task]
    if not (HERE / res_f).exists() or not (HERE / ho_f).exists():
        return []
    results = json.loads((HERE / res_f).read_text())
    held = {r["name"]: r.get("outcomes") for r in json.loads((HERE / ho_f).read_text())["rows"]}
    cells = []
    for r in results["rows"]:
        if r["kind"] != "artifact" or not held.get(r["name"]):
            continue
        for req, h in held[r["name"]].items():
            v = {g: r[g].get(req) for g in GRADERS}
            if all(x in DECIDED for x in v.values()) and h is not None:
                cells.append({"task": task, "controller": r["name"], "req": req, "H": float(h),
                              **{g: v[g] == "fail" for g in GRADERS}})
    return cells


def _strata(cells: list[dict]) -> dict[tuple, list[dict]]:
    out: dict[tuple, list[dict]] = {}
    for c in cells:
        out.setdefault((c["task"], c["req"]), []).append(c)
    return out


def pooled_auc(cells: list[dict], flag: str) -> tuple[float | None, int]:
    """Stratified AUC that flagged cells have higher H; and the number of pairs."""
    num = 0.0
    pairs = 0
    for group in _strata(cells).values():
        pos = [c["H"] for c in group if c[flag]]
        neg = [c["H"] for c in group if not c[flag]]
        for p in pos:
            for n in neg:
                num += 1.0 if p > n else 0.5 if p == n else 0.0
        pairs += len(pos) * len(neg)
    return (num / pairs if pairs else None), pairs


def permutation_p(cells: list[dict], flag: str, rng: random.Random) -> float | None:
    obs, pairs = pooled_auc(cells, flag)
    if obs is None:
        return None
    groups = list(_strata(cells).values())
    hits = 0
    for _ in range(N_PERM):
        perm = []
        for g in groups:
            labels = [c[flag] for c in g]
            rng.shuffle(labels)
            perm += [dict(c, _p=l) for c, l in zip(g, labels)]
        a, _ = pooled_auc(perm, "_p")
        hits += a is not None and a >= obs - 1e-12
    return (hits + 1) / (N_PERM + 1)


def bootstrap_diff(cells: list[dict], a: str, b: str, rng: random.Random) -> list[float] | None:
    ctrls = sorted({(c["task"], c["controller"]) for c in cells})
    by = {k: [c for c in cells if (c["task"], c["controller"]) == k] for k in ctrls}
    diffs = []
    for _ in range(N_BOOT):
        sample = [c for k in (rng.choice(ctrls) for _ in ctrls) for c in by[k]]
        x, _ = pooled_auc(sample, a)
        y, _ = pooled_auc(sample, b)
        if x is not None and y is not None:
            diffs.append(x - y)
    if not diffs:
        return None
    diffs.sort()
    return [diffs[int(0.025 * len(diffs))], diffs[int(0.975 * len(diffs)) - 1]]


def _ranks(xs: list[float]) -> list[float]:
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for k in range(i, j + 1):
            r[order[k]] = (i + j) / 2
        i = j + 1
    return r


def spearman(x: list[float], y: list[float]) -> float | None:
    if len(x) < 3:
        return None
    rx, ry = _ranks(x), _ranks(y)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    sxy = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    sxx = sum((a - mx) ** 2 for a in rx) ** 0.5
    syy = sum((b - my) ** 2 for b in ry) ** 0.5
    return sxy / (sxx * syy) if sxx and syy else None


def controller_level(cells: list[dict]) -> dict[str, Any]:
    pct: dict[tuple, float] = {}
    for group in _strata(cells).values():
        rk = _ranks([c["H"] for c in group])
        for c, r in zip(group, rk):
            pct[(c["task"], c["controller"], c["req"])] = r / max(1, len(group) - 1)
    ctrls = sorted({(c["task"], c["controller"]) for c in cells})
    comp, nfail = [], {g: [] for g in GRADERS}
    for k in ctrls:
        mine = [c for c in cells if (c["task"], c["controller"]) == k]
        comp.append(sum(pct[(c["task"], c["controller"], c["req"])] for c in mine) / len(mine))
        for g in GRADERS:
            nfail[g].append(sum(c[g] for c in mine))
    return {"n_controllers": len(ctrls), **{f"spearman_{g}": spearman(nfail[g], comp) for g in GRADERS}}


def analyze(cells: list[dict], seed: int = 2027) -> dict[str, Any]:
    rng = random.Random(seed)
    basic_pass = [c for c in cells if not c["single_trace"]]
    auc1, pairs1 = pooled_auc(basic_pass, "paired")
    out: dict[str, Any] = {
        "n_cells": len(cells), "n_controllers": len({(c["task"], c["controller"]) for c in cells}),
        "H1": {"n_cells": len(basic_pass), "paired_fail": sum(c["paired"] for c in basic_pass),
               "pairs": pairs1, "auc": auc1, "p_one_sided": permutation_p(basic_pass, "paired", rng)},
        "H2": {g: {"auc": pooled_auc(cells, g)[0], "flagged": sum(c[g] for c in cells)} for g in GRADERS},
    }
    out["H2"]["paired_minus_basic_ci95"] = bootstrap_diff(cells, "paired", "single_trace", rng)
    out["H2"]["paired_minus_strong_ci95"] = bootstrap_diff(cells, "paired", "single_trace_strong", rng)
    out["H3"] = controller_level(cells)
    out["per_requirement"] = {
        f"{t}/{r}": {"n": len(g), "paired_fail": sum(c["paired"] for c in g),
                     "auc_paired": pooled_auc(g, "paired")[0], "auc_basic": pooled_auc(g, "single_trace")[0]}
        for (t, r), g in sorted(_strata(cells).items())}
    return out


def main() -> int:
    report: dict[str, Any] = {"prereg": "paper/RQ3_PREREG.md", "tasks": {}}
    pooled: list[dict] = []
    for task in TASKS:
        cells = load_cells(task)
        if cells:
            report["tasks"][task] = analyze(cells)
            pooled += cells
    if pooled:
        report["pooled"] = analyze(pooled)
    (HERE / "rq3.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({k: (v if k != "tasks" else {t: {x: y for x, y in a.items() if x != "per_requirement"}
                                                    for t, a in v.items()}) for k, v in report.items()}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
