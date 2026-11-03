"""Blinded independent adjudication of pilot verdicts.

``build``: sample (controller, requirement) items from both tasks' study
results, write one self-contained packet directory per rater (task text, the
requirement under review, the controller's code, the task simulator) and a
separate answer key. Packets carry opaque item ids and no verdicts; raters
never see probes, predicates, results, or the paper.

``score``: read the raters' JSON answers and the key; report inter-rater
agreement (Fleiss' kappa), attention-check accuracy, and agreement of the
rater majority with the paired verdict and with the basic single-trace
verdict, per item category.

    python -m diptych.pilot.adjudication build --out /tmp/adj --raters 5
    python -m diptych.pilot.adjudication score --out /tmp/adj

Raters here are model-based agents, not people: the result is an
independent re-judgment of the ground truth, not human validation.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import shutil
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from diptych.pilot.tasks import TASKS

HERE = Path(__file__).resolve().parent
SEED = 2026

# Requirement number in each TASK.md for each study key.
REQ_NUMBER = {
    "concurrency": {"SIGNFLIP": 1, "TRAJSWAP": 2, "SATEXTEND": 3, "VARSCALE": 4, "HISTSWAP": 5,
                    "RESEED": 6, "FREEZEDRY": 7, "SCHEMAX": 8},
    "congestion": {k: i + 1 for i, k in enumerate(
        ("full-backoff", "loss-reduction", "intra-fairness", "harm-throughput", "harm-latency",
         "short-flows", "delay-change", "path-change"))},
    "autoscale": {k: i + 1 for i, k in enumerate(
        ("tolerance", "scale-up-rate", "downscale-stabilization", "missing-metrics", "not-ready",
         "window-history", "ratio-only", "flapping"))},
}
PAIRED_KEYS = {
    "concurrency": ("SIGNFLIP", "TRAJSWAP", "VARSCALE", "SATEXTEND", "HISTSWAP"),
    "congestion": ("harm-throughput", "harm-latency", "short-flows", "delay-change", "path-change"),
    "autoscale": ("missing-metrics", "not-ready", "window-history", "ratio-only", "flapping"),
}
LABEL = {"concurrency": "A", "congestion": "B", "autoscale": "C"}

INSTRUCTIONS = """# Instructions for raters

You are an independent reviewer. Each directory `items/<id>/` holds one
review item: a controller written for a task, and ONE requirement from that
task. Decide whether the controller **satisfies** or **violates** that
requirement as stated.

Each item contains:
- `TASK.md` — the full task the controller was written for
- `REQUIREMENT.md` — the single requirement under review
- `controller.py` — the controller

The task's simulator is in `../../task_<A|B|C>/` (`plant.py`, `sim.py`, and for
task B `reference.py`, the "standard" algorithm). You may read the code,
reason about it, and write and run your own experiments in your workspace
with `python3` (standard library only). Judge the requirement exactly as
worded in `REQUIREMENT.md`, including any thresholds; where it is silent,
use the most natural reading and say so.

Rules:
- Work only inside this packet directory. Do not open any other file or
  directory on this machine, and do not use the network.
- Do not guess from file names or ids; they carry no information.
- If you cannot decide with reasonable confidence, answer `unsure`.

Write your answers to `answers.json` in this packet directory:

```json
{
  "rater": "<your rater id>",
  "items": {
    "<id>": {"verdict": "violates" | "satisfies" | "unsure",
             "confidence": 1-5,
             "method": "code reading" | "experiment" | "both",
             "rationale": "2-4 sentences: the evidence that decided it"}
  }
}
```

Answer every item.
"""


HUMAN_INSTRUCTIONS = """# Instructions for raters

Thank you for rating. Each directory `items/<id>/` holds one review item: a
controller written for a task, and ONE requirement from that task. Decide
whether the controller **satisfies** or **violates** that requirement as
stated.

Each item contains `TASK.md` (the full task the controller was written for),
`REQUIREMENT.md` (the requirement under review) and `controller.py`. The
task's simulator is in `task_<A|B|C>/` at the top of this packet (`plant.py`,
`sim.py`, and for task B `reference.py`). You may read the code, reason
about it, and run your own experiments with `python3` (standard library
only); copying the simulator next to the controller and driving it from a
short script is usually the quickest check.

Rules:
- Rate independently. Do not discuss items with the other rater until both
  of you have finished.
- Use only this packet: not the DIPTYCH repository, its results, the paper,
  or an AI assistant. Item ids and order carry no information.
- Judge the requirement exactly as worded, including thresholds; where it is
  silent, use the most natural reading and say so in the rationale.
- If you cannot decide with reasonable confidence, answer `unsure`.
- Budget about 4 minutes per item; take breaks. Stop and resume any time.

Record answers with the rating tool, run from the repository root:

    python3 -m diptych.pilot.rate --packet PATH/TO/THIS/PACKET --rater YOUR_NAME

It shows one item at a time, saves after every answer to `answers.json` in
this packet, and resumes where you stopped. Send `answers.json` back when
done.
"""


def _strip(src: str) -> str:
    """Source without docstrings, bare string statements, or comments."""
    import ast

    def is_doc(n: ast.AST) -> bool:
        return isinstance(n, ast.Expr) and isinstance(getattr(n, "value", None), ast.Constant) \
            and isinstance(n.value.value, str)

    tree = ast.parse(src)
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(body, list):
            node.body = [n for n in body if not is_doc(n)] or [ast.Pass()]
    return ast.unparse(tree) + "\n"


def standalone_control(path: Path) -> str:
    """A control as one self-contained module with nothing revealing its role.

    Controls subclass a base in the harness and say what they violate in
    their docstrings; raters must see plain code like any other controller.
    """
    src = path.read_text()
    bases = {
        "from diptych.pilot.controls.conforming import Controller as _Conforming": (HERE / "controls" / "conforming.py", "_Conforming"),
        "from diptych.pilot.congestion.reference import Controller as _Reno": (HERE / "congestion" / "reference.py", "_Reno"),
        "from diptych.pilot.congestion.reference import Controller  # noqa: F401": (HERE / "congestion" / "reference.py", None),
        "from diptych.pilot.autoscale.controls.conforming import Controller as _HPA": (HERE / "autoscale" / "controls" / "conforming.py", "_HPA"),
    }
    for line, (base_path, alias) in bases.items():
        if line in src:
            base = base_path.read_text()
            if alias is None:  # pure re-export of the base
                return _strip(base)
            base = base.replace("class Controller:", "class _Base:")
            rest = src.replace(line, "").replace(alias, "_Base")
            return _strip(base + "\n\n" + rest)
    return _strip(src)


def _requirement_text(task: str, number: int) -> str:
    text = TASKS[task].text
    m = re.search(rf"^{number}\. (.*?)(?=^\d+\. |\n## |\Z)", text, re.S | re.M)
    if not m:
        raise ValueError(f"requirement {number} not found in {task} TASK.md")
    return " ".join(m.group(1).split())


def _load(task: str) -> list[dict]:
    d = json.loads((HERE / TASKS[task].results).read_text())
    return d["rows"]


def _cells(rows: list[dict], task: str, keys: tuple[str, ...]) -> list[tuple[dict, str]]:
    return [(r, k) for r in rows if r["kind"] == "artifact" for k in keys]


def select_items(rng: random.Random) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []

    def add(task: str, r: dict, key: str, category: str) -> None:
        items.append({"task": task, "row": r["name"], "artifact": r["artifact"], "key": key,
                      "category": category, "paired": r["paired"][key],
                      "basic": r["single_trace"][key], "strong": r["single_trace_strong"][key]})

    for task in ("concurrency", "congestion"):
        rows = _load(task)
        cells = _cells(rows, task, PAIRED_KEYS[task])
        po = [(r, k) for r, k in cells if r["paired"][k] == "fail" and r["single_trace"][k] == "pass"]
        if task == "concurrency":  # stratify the 45 paired-only cells
            quota = {"TRAJSWAP": 5, "VARSCALE": 3, "SIGNFLIP": 2, "SATEXTEND": 1, "HISTSWAP": 1}
            for key, n in quota.items():
                pool = [c for c in po if c[1] == key]
                for r, k in rng.sample(pool, min(n, len(pool))):
                    add(task, r, k, "paired_only")
        else:
            for r, k in po:
                add(task, r, k, "paired_only")
        both_pass = [(r, k) for r, k in cells if r["paired"][k] == "pass" and r["single_trace"][k] == "pass"]
        both_fail = [(r, k) for r, k in cells if r["paired"][k] == "fail" and r["single_trace"][k] == "fail"]
        strong_key = "SIGNFLIP" if task == "concurrency" else "harm-throughput"
        strong_only = [(r, k) for r, k in cells if k == strong_key
                       and r["paired"][k] == "pass" and r["single_trace_strong"][k] == "fail"]
        for pool, n, cat in ((both_pass, 4, "both_pass"), (both_fail, 2, "both_fail"),
                             (strong_only, 2, "strong_only")):
            for r, k in rng.sample(pool, min(n, len(pool))):
                add(task, r, k, cat)
        # Attention checks: controls with known answers.
        ctrl = {r["name"]: r for r in rows if r["kind"] == "control"}
        conf_key, viol = PAIRED_KEYS[task][0], None
        if task == "concurrency":
            viol = ("control:violating-trajswap", "TRAJSWAP")
        else:
            viol = ("control:violating-path_change", "path-change")
            conf_key = "path-change"
        add(task, ctrl["control:conforming"], conf_key, "control")
        add(task, ctrl[viol[0]], viol[1], "control")
    return items


def _write_packets(out: Path, items: list[dict], names: list[str], instructions: str, seed: int) -> None:
    out.mkdir(parents=True, exist_ok=True)
    key = {it["id"]: it for it in items}
    (out / "KEY.json").write_text(json.dumps(key, indent=2, sort_keys=True))
    tasks = sorted({it["task"] for it in items}, key=lambda t: LABEL[t])
    for n, name in enumerate(names, 1):
        pk = out / f"packet-{name}"
        if pk.exists():
            shutil.rmtree(pk)
        (pk / "items").mkdir(parents=True)
        (pk / "INSTRUCTIONS.md").write_text(instructions)
        for task in tasks:
            td = pk / f"task_{LABEL[task]}"
            td.mkdir()
            for dest, src in TASKS[task].workspace.items():
                if dest != "TASK.md":
                    shutil.copy(TASKS[task].root / src, td / dest)
        order = list(items)
        random.Random(seed + n).shuffle(order)  # each rater sees a different order
        for it in order:
            d = pk / "items" / it["id"]
            d.mkdir()
            task = it["task"]
            num = REQ_NUMBER[task][it["key"]]
            (d / "TASK.md").write_text(TASKS[task].text)
            (d / "REQUIREMENT.md").write_text(
                f"# Requirement under review (task {LABEL[task]})\n\n"
                f"Requirement {num} of TASK.md:\n\n{_requirement_text(task, num)}\n\n"
                f"The task simulator is in ../../task_{LABEL[task]}/.\n")
            src = HERE.parents[1] / it["artifact"]
            if it["category"] == "control":
                (d / "controller.py").write_text(standalone_control(src))
            else:
                shutil.copy(src, d / "controller.py")
        (pk / "ORDER.txt").write_text("\n".join(it["id"] for it in order) + "\n")
    cats = Counter((it["task"], it["category"]) for it in items)
    print(json.dumps({"items": len(items), "raters": names,
                      "by_category": {f"{t}/{c}": n for (t, c), n in sorted(cats.items())}}, indent=2))


def build(out: Path, raters: int) -> None:
    rng = random.Random(SEED)
    items = select_items(rng)
    ids = rng.sample(range(100, 1000), len(items))
    for it, i in zip(items, ids):
        it["id"] = f"item-{i}"
    _write_packets(out, items, [f"r{n}" for n in range(1, raters + 1)], INSTRUCTIONS, SEED)


def select_autoscale(rng: random.Random) -> list[dict[str, Any]]:
    """Task C items for the human study, by the same categories."""
    task = "autoscale"
    rows = _load(task)
    cells = _cells(rows, task, PAIRED_KEYS[task])
    items: list[dict[str, Any]] = []

    def add(r: dict, key: str, category: str) -> None:
        items.append({"task": task, "row": r["name"], "artifact": r["artifact"], "key": key,
                      "category": category, "paired": r["paired"][key],
                      "basic": r["single_trace"][key], "strong": r["single_trace_strong"][key]})

    pools = {
        "paired_only": [(r, k) for r, k in cells if r["paired"][k] == "fail" and r["single_trace"][k] == "pass"],
        "both_pass": [(r, k) for r, k in cells if r["paired"][k] == "pass" and r["single_trace"][k] == "pass"],
        "both_fail": [(r, k) for r, k in cells if r["paired"][k] == "fail" and r["single_trace"][k] == "fail"],
        "strong_only": [(r, k) for r, k in cells if r["paired"][k] == "pass" and r["single_trace_strong"][k] == "fail"],
    }
    for cat, n in (("paired_only", 8), ("both_pass", 5), ("both_fail", 3), ("strong_only", 2)):
        for r, k in rng.sample(pools[cat], min(n, len(pools[cat]))):
            add(r, k, cat)
    ctrl = {r["name"]: r for r in rows if r["kind"] == "control"}
    add(ctrl["control:conforming"], "missing-metrics", "control")
    add(ctrl["control:violating-ratio_only"], "ratio-only", "control")
    return items


def build_human(out: Path, names: list[str]) -> None:
    """The 42 model-adjudicated items (same ids) plus Task C items."""
    prior = json.loads((HERE / "adjudication_data" / "KEY.json").read_text())
    items = [dict(it) for it in prior.values()]
    rng = random.Random(SEED + 100)
    new = select_autoscale(rng)
    used = {int(i.split("-")[1]) for i in prior}
    ids = rng.sample(sorted(set(range(100, 1000)) - used), len(new))
    for it, i in zip(new, ids):
        it["id"] = f"item-{i}"
    _write_packets(out, items + new, names, HUMAN_INSTRUCTIONS, SEED + 100)


def fleiss_kappa(table: list[list[int]]) -> float | None:
    """Fleiss' kappa for items x categories count table (equal raters per item)."""
    table = [row for row in table if sum(row) > 1]
    if not table:
        return None
    N = len(table)
    k = len(table[0])
    p_j = [sum(row[j] for row in table) / sum(sum(row) for row in table) for j in range(k)]
    P_i = [(sum(c * c for c in row) - sum(row)) / (sum(row) * (sum(row) - 1)) for row in table]
    P_bar, P_e = sum(P_i) / N, sum(p * p for p in p_j)
    return None if P_e == 1 else (P_bar - P_e) / (1 - P_e)


def score(out: Path) -> dict[str, Any]:
    key = json.loads((out / "KEY.json").read_text())
    answers = {}
    for f in sorted(out.glob("packet-*/answers.json")):
        a = json.loads(f.read_text())
        answers[f.parent.name] = a.get("items", {})
    cats = ("violates", "satisfies", "unsure")
    rows, table = [], []
    for iid, it in sorted(key.items()):
        votes = [answers[r].get(iid, {}).get("verdict", "missing") for r in answers]
        c = Counter(v if v in cats else "unsure" for v in votes)
        table.append([c[x] for x in cats])
        decided = {x: c[x] for x in ("violates", "satisfies")}
        majority = None
        if decided["violates"] != decided["satisfies"]:
            majority = max(decided, key=decided.get)
        truth = {"fail": "violates", "pass": "satisfies"}
        rows.append({**{k: it[k] for k in ("id", "task", "category", "key", "paired", "basic", "strong", "row")},
                     "votes": dict(c), "majority": majority,
                     "agrees_paired": majority == truth.get(it["paired"]) if majority else None,
                     "agrees_basic": majority == truth.get(it["basic"]) if majority else None,
                     "agrees_strong": majority == truth.get(it["strong"]) if majority else None})
    summary: dict[str, Any] = {"raters": sorted(answers), "items": len(rows),
                               "fleiss_kappa": fleiss_kappa(table)}
    by: dict[str, Any] = {}
    for r in rows:
        g = by.setdefault(f"{LABEL[r['task']]}/{r['category']}", Counter())
        g["n"] += 1
        g["majority_decided"] += r["majority"] is not None
        g["agrees_paired"] += bool(r["agrees_paired"])
        g["agrees_basic"] += bool(r["agrees_basic"])
        g["agrees_strong"] += bool(r["agrees_strong"])
        g["unanimous"] += max(r["votes"].values()) == len(answers)
    summary["by_category"] = {k: dict(v) for k, v in sorted(by.items())}
    decided = [r for r in rows if r["majority"]]
    summary["overall"] = {
        "majority_decided": len(decided),
        "agrees_paired": sum(bool(r["agrees_paired"]) for r in decided),
        "agrees_basic": sum(bool(r["agrees_basic"]) for r in decided),
    }
    report = {"summary": summary, "items": rows}
    (out / "SCORE.json").write_text(json.dumps(report, indent=2, sort_keys=True))
    return report


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="diptych.pilot.adjudication")
    p.add_argument("command", choices=("build", "build-human", "score"))
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--raters", type=int, default=5)
    p.add_argument("--names", nargs="+", default=["abhinav", "abhishek"], help="human raters (build-human)")
    a = p.parse_args(argv)
    if a.command == "build":
        build(a.out, a.raters)
    elif a.command == "build-human":
        build_human(a.out, a.names)
    else:
        print(json.dumps(score(a.out)["summary"], indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
