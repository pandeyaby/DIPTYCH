"""Pilot study: grade controls and generated artifacts, paired vs single-trace.

Every controller is evaluated in its own subprocess (``worker --mode
evaluate``) with a timeout, so a hanging or crashing artifact cannot affect
the others. Outputs ``results.json`` next to this file and prints the tables
the paper reports.

    python -m diptych.pilot.study            # controls + all artifacts
    python -m diptych.pilot.study --controls-only
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import itertools
import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from diptych import OPERATORS

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
RESULTS = HERE / "results.json"
DECIDED = ("pass", "fail")


def _controls() -> list[tuple[str, str, str | None]]:
    """(name, path, violated operator or None)."""
    out = [("control:conforming", str(HERE / "controls" / "conforming.py"), None)]
    for p in sorted((HERE / "controls" / "violating").glob("*.py")):
        if p.stem != "__init__":
            out.append((f"control:violating-{p.stem}", str(p), p.stem.upper()))
    return out


def _artifacts() -> list[tuple[str, str]]:
    manifest = HERE / "artifacts" / "manifest.json"
    if not manifest.exists():
        return []
    out = []
    for r in json.loads(manifest.read_text()):
        if r.get("code_block"):
            out.append((f"{r['model']}#{r['sample']}", str(HERE / r["path"])))
    return out


def evaluate_isolated(path: str, timeout: int = 600) -> dict[str, Any]:
    env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONHASHSEED="0")
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "diptych.pilot.worker", "--artifact", path, "--mode", "evaluate"],
            capture_output=True, text=True, env=env, timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired:
        return _all_error(path, "evaluation timed out")
    if proc.returncode != 0:
        return _all_error(path, proc.stderr.strip()[-300:])
    return json.loads(proc.stdout)


def _all_error(path: str, why: str) -> dict[str, Any]:
    return {"artifact": path, "paired": {o: "error" for o in OPERATORS},
            "single_trace": {o: "error" for o in OPERATORS}, "errors": [why]}


def control_power(rows: list[dict]) -> dict[str, Any]:
    """Per grader: own-axis detection on violators, false alarms on conforming."""
    out: dict[str, Any] = {}
    for grader in ("paired", "single_trace"):
        conf = next(r for r in rows if r["violates"] is None)
        viol = [r for r in rows if r["violates"]]
        caught = [r["violates"] for r in viol if r[grader][r["violates"]] == "fail"]
        off_axis = sum(1 for r in viol for op in OPERATORS
                       if op != r["violates"] and r[grader][op] == "fail")
        out[grader] = {
            "own_axis_caught": len(caught), "violators": len(viol),
            "caught": sorted(caught),
            "conforming_false_alarms": sorted(op for op in OPERATORS if conf[grader][op] == "fail"),
            "off_axis_fails_on_violators": off_axis,
        }
    return out


def artifact_metrics(rows: list[dict]) -> dict[str, Any]:
    per_op: dict[str, Any] = {}
    for op in OPERATORS:
        paired = Counter(r["paired"][op] for r in rows)
        single = Counter(r["single_trace"][op] for r in rows)
        both = [r for r in rows if r["paired"][op] in DECIDED and r["single_trace"][op] in DECIDED]
        confusion = Counter(f"{r['single_trace'][op]}/{r['paired'][op]}" for r in both)
        per_op[op] = {
            "paired": dict(paired), "single_trace": dict(single),
            "decided_by_both": len(both),
            "single_pass_paired_fail": confusion["pass/fail"],
            "single_fail_paired_pass": confusion["fail/pass"],
            "agree": confusion["pass/pass"] + confusion["fail/fail"],
        }
    # Separation index (paper §Metrics): among artifact pairs whose single-trace
    # verdict vectors are identical, the fraction whose paired vectors differ.
    eq_pairs = diff = 0
    for a, b in itertools.combinations(rows, 2):
        if a["single_trace"] == b["single_trace"]:
            eq_pairs += 1
            diff += a["paired"] != b["paired"]
    fully_single_pass = [r for r in rows if all(v == "pass" for v in r["single_trace"].values())]
    seed_cells = seed_stable = 0
    for r in rows:
        by_seed = r.get("paired_by_seed") or {}
        for op in (next(iter(by_seed.values()), {}) if by_seed else {}):
            seed_cells += 1
            seed_stable += len({v[op] for v in by_seed.values()}) == 1
    ticks = [r["ticks"] for r in rows if r.get("ticks")]
    forked = sum(t["forked"] for t in ticks)
    naive = sum(t["naive_equivalent"] for t in ticks)
    n_verdicts = len(rows) * len(OPERATORS)
    return {
        "n_artifacts": len(rows),
        "per_operator": per_op,
        "separation": {"trace_equivalent_pairs": eq_pairs, "hyper_distinct": diff,
                       "index": (diff / eq_pairs) if eq_pairs else None},
        "all_single_trace_pass": len(fully_single_pass),
        "all_single_trace_pass_but_paired_fail": sum(
            1 for r in fully_single_pass if "fail" in r["paired"].values()),
        "inconclusive_rate": sum(v == "inconclusive" for r in rows for v in r["paired"].values()) / n_verdicts
        if rows else None,
        "error_rate": sum(v == "error" for r in rows for v in r["paired"].values()) / n_verdicts
        if rows else None,
        "seed_stability": {"cells": seed_cells, "same_verdict_all_seeds": seed_stable,
                           "rate": (seed_stable / seed_cells) if seed_cells else None},
        "fork_fidelity_ok": sum(1 for r in rows if (r.get("fork_fidelity") or {}).get("ok")),
        "ticks": {"forked": forked, "naive_equivalent": naive,
                  "alpha": (naive / forked) if forked else None},
    }


def _fmt(v: str) -> str:
    return {"pass": ".", "fail": "F", "inconclusive": "?", "error": "E"}.get(v, "?")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="diptych.pilot.study")
    p.add_argument("--controls-only", action="store_true")
    p.add_argument("--jobs", type=int, default=4)
    args = p.parse_args(argv)
    targets = [(n, path, v) for n, path, v in _controls()]
    if not args.controls_only:
        targets += [(n, path, "artifact") for n, path in _artifacts()]
    with cf.ThreadPoolExecutor(max_workers=args.jobs) as ex:
        evals = list(ex.map(lambda t: evaluate_isolated(t[1]), targets))
    rows = []
    for (name, path, v), ev in zip(targets, evals):
        ev["name"] = name
        ev["artifact"] = str(Path(path).relative_to(ROOT))
        ev["violates"] = None if v in (None, "artifact") else v
        ev["kind"] = "artifact" if v == "artifact" else "control"
        rows.append(ev)
    controls = [r for r in rows if r["kind"] == "control"]
    artifacts = [r for r in rows if r["kind"] == "artifact"]
    report = {"study_schema": "1.0", "operators": list(OPERATORS),
              "controls": control_power(controls),
              "artifacts": artifact_metrics(artifacts) if artifacts else None,
              "rows": rows}
    RESULTS.write_text(json.dumps(report, indent=2, sort_keys=True, default=repr) + "\n")
    print("operators:", " ".join(OPERATORS))
    for r in rows:
        print(f"{r['name']:<42} paired={''.join(_fmt(r['paired'][o]) for o in OPERATORS)} "
              f"single={''.join(_fmt(r['single_trace'][o]) for o in OPERATORS)}"
              + (f"  errors={r['errors'][:1]}" if r.get("errors") else ""))
    print(json.dumps({"controls": report["controls"], "artifacts": {
        k: v for k, v in (report["artifacts"] or {}).items() if k != "per_operator"}}, indent=2))
    if report["artifacts"]:
        for op, m in report["artifacts"]["per_operator"].items():
            print(f"{op:<10} paired={m['paired']} single={m['single_trace']} "
                  f"S-pass/P-fail={m['single_pass_paired_fail']} S-fail/P-pass={m['single_fail_paired_pass']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
