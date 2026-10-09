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


def _group(r: dict) -> str:
    return f"{r.get('condition', 'oneshot')}-{r.get('agent', 'claude')}"


def _artifacts() -> list[tuple[str, str, str]]:
    """(name, path, group) for every generated controller."""
    manifest = HERE / "artifacts" / "manifest.json"
    if not manifest.exists():
        return []
    out = []
    for r in json.loads(manifest.read_text()):
        if r.get("code_block"):
            out.append((f"{_group(r)}:{r['model']}#{r['sample']}", str(HERE / r["path"]), _group(r)))
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
    for grader in ("paired", "single_trace", "single_trace_strong"):
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


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    """Wilson score 95% interval for k successes in n trials."""
    if n == 0:
        return None
    ph = k / n
    den = 1 + z * z / n
    c = (ph + z * z / (2 * n)) / den
    h = z * ((ph * (1 - ph) / n + z * z / (4 * n * n)) ** 0.5) / den
    return (max(0.0, c - h), min(1.0, c + h))


def mcnemar_exact(b: int, c: int) -> float:
    """Two-sided exact McNemar p-value for discordant counts b, c."""
    from math import comb
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    p = sum(comb(n, i) for i in range(k + 1)) / 2 ** n
    return min(1.0, 2 * p)


def artifact_metrics(rows: list[dict], single: str = "single_trace") -> dict[str, Any]:
    per_op: dict[str, Any] = {}
    for op in OPERATORS:
        paired = Counter(r["paired"][op] for r in rows)
        single_c = Counter(r[single][op] for r in rows)
        both = [r for r in rows if r["paired"][op] in DECIDED and r[single][op] in DECIDED]
        confusion = Counter(f"{r[single][op]}/{r['paired'][op]}" for r in both)
        per_op[op] = {
            "paired": dict(paired), "single_trace": dict(single_c),
            "decided_by_both": len(both),
            "single_pass_paired_fail": confusion["pass/fail"],
            "single_fail_paired_pass": confusion["fail/pass"],
            "agree": confusion["pass/pass"] + confusion["fail/fail"],
        }
    # Separation index (paper §Metrics): among artifact pairs whose single-trace
    # verdict vectors are identical, the fraction whose paired vectors differ.
    eq_pairs = diff = 0
    for a, b in itertools.combinations(rows, 2):
        if a[single] == b[single]:
            eq_pairs += 1
            diff += a["paired"] != b["paired"]
    fully_single_pass = [r for r in rows if all(v == "pass" for v in r[single].values())]
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
    k_fail = sum(1 for r in fully_single_pass if "fail" in r["paired"].values())
    # Artifact-level McNemar: paired flags a violation single-trace misses (b)
    # vs. single-trace flags one paired does not (c), counted per artifact.
    b = sum(1 for r in rows if any(r["paired"][o] == "fail" and r[single][o] == "pass" for o in OPERATORS))
    c = sum(1 for r in rows if any(r[single][o] == "fail" and r["paired"][o] == "pass" for o in OPERATORS))
    return {
        "single_grader": single,
        "n_artifacts": len(rows),
        "per_operator": per_op,
        "all_single_pass_paired_fail_ci95": wilson(k_fail, len(fully_single_pass)),
        "artifact_mcnemar": {"paired_only": b, "single_only": c, "p_exact": mcnemar_exact(b, c)},
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
    targets = [(n, path, v, None) for n, path, v in _controls()]
    if not args.controls_only:
        targets += [(n, path, "artifact", g) for n, path, g in _artifacts()]
    with cf.ThreadPoolExecutor(max_workers=args.jobs) as ex:
        evals = list(ex.map(lambda t: evaluate_isolated(t[1]), targets))
    rows = []
    for (name, path, v, group), ev in zip(targets, evals):
        ev["name"] = name
        ev["group"] = group
        ev["artifact"] = str(Path(path).relative_to(ROOT))
        ev["violates"] = None if v in (None, "artifact") else v
        ev["kind"] = "artifact" if v == "artifact" else "control"
        rows.append(ev)
    controls = [r for r in rows if r["kind"] == "control"]
    artifacts = [r for r in rows if r["kind"] == "artifact"]
    groups = sorted({r["group"] for r in artifacts})
    report = {"study_schema": "1.1", "operators": list(OPERATORS),
              "controls": control_power(controls),
              "artifacts": artifact_metrics(artifacts) if artifacts else None,
              "artifacts_strong": artifact_metrics(artifacts, "single_trace_strong") if artifacts else None,
              "by_group": {g: artifact_metrics([r for r in artifacts if r["group"] == g]) for g in groups},
              "by_group_strong": {g: artifact_metrics([r for r in artifacts if r["group"] == g],
                                                      "single_trace_strong") for g in groups},
              "rows": rows}
    RESULTS.write_text(json.dumps(report, indent=2, sort_keys=True, default=repr) + "\n")
    print("operators:", " ".join(OPERATORS))
    for r in rows:
        print(f"{r['name']:<42} paired={''.join(_fmt(r['paired'][o]) for o in OPERATORS)} "
              f"single={''.join(_fmt(r['single_trace'][o]) for o in OPERATORS)}"
              + (f"  errors={r['errors'][:1]}" if r.get("errors") else ""))
    print(json.dumps({"controls": report["controls"], "artifacts": {
        k: v for k, v in (report["artifacts"] or {}).items() if k != "per_operator"}}, indent=2))
    for label, key in (("basic", "by_group"), ("strong", "by_group_strong")):
        for g, m in report[key].items():
            print(f"[{label}:{g}] n={m['n_artifacts']} all_single_pass={m['all_single_trace_pass']} "
                  f"of_which_paired_fail={m['all_single_trace_pass_but_paired_fail']} sep={m['separation']['index']}")
        agg = report["artifacts" if key == "by_group" else "artifacts_strong"]
        if agg:
            print(f"[{label}:ALL] all_single_pass={agg['all_single_trace_pass']} "
                  f"paired_fail={agg['all_single_trace_pass_but_paired_fail']} "
                  f"ci95={agg['all_single_pass_paired_fail_ci95']} sep={agg['separation']} "
                  f"mcnemar={agg['artifact_mcnemar']}")
            for op, m in agg["per_operator"].items():
                print(f"   {op:<10} paired_fail={m['paired'].get('fail', 0)} single_fail={m['single_trace'].get('fail', 0)} "
                      f"missed={m['single_pass_paired_fail']} single_only={m['single_fail_paired_pass']}")
    for g, m in report["by_group"].items():
        print(f"[{g}] n={m['n_artifacts']} all_single_pass={m['all_single_trace_pass']} "
              f"of_which_paired_fail={m['all_single_trace_pass_but_paired_fail']} "
              f"separation={m['separation']} paired_fail_any="
              f"{sum(1 for r in artifacts if r['group'] == g and 'fail' in r['paired'].values())}")
    if report["artifacts"]:
        for op, m in report["artifacts"]["per_operator"].items():
            print(f"{op:<10} paired={m['paired']} single={m['single_trace']} "
                  f"S-pass/P-fail={m['single_pass_paired_fail']} S-fail/P-pass={m['single_fail_paired_pass']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
