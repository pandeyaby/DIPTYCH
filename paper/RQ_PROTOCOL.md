# RQ1–RQ5 protocol scaffolding — DIPTYCH

Companion to Section VII (`sec:rqs`) in
[`one-trace-is-not-enough.tex`](one-trace-is-not-enough.tex).

This file states **how the harness answers each research question** with
controls + adapters. It does **not** invent AUROC, accuracy, separation
indices, or model ranks. Result cells stay **N/A** / protocol-only until a
real model study lands measured data.

**Offline fact (not an RQ answer):** green×8×3 in `coverage/matrix.json` =
twin contrast + `gate_axis_mutate` at pins in `adapters/PINS.md`.

---

## Shared machinery (all RQs)

| Piece | Path / contract |
|-------|-----------------|
| Schema / verdicts | `diptych_schema=0.2`; `pass` \| `fail` \| `inconclusive` |
| Couplings | `open_loop` \| `crn_closed_loop` (`diptych.COUPLINGS`) |
| Operators | `diptych.OPERATORS` (8); specs in `ops/*/spec.yaml` |
| Handwritten controls | `controls/<OP>/{conforming,violating}.py` |
| Core fixtures | `diptych-probes/<OP>/{conforming,violating}/` |
| Adapters (pins only) | ZeroDay@`fb5b39da`, AOMB@`667e475` — not vendored |
| Green rule | conforming→pass, violating→fail, axis power; `inconclusive`≠green |
| Cost model | Definitions in tex (`def:cost`); protocol formula, not measured $ |

---

## RQ1 — Separation under hyperproperty probing

| Field | Protocol |
|-------|----------|
| **Question** | Do artifacts that trace-property grading ranks as equivalent separate under hyperproperty probing? |
| **Metric** | Separation index (paper §metrics): fraction of trace-equivalent pairs with disagreeing hyperproperty score vectors |
| **Harness answer** | Grade each subject with all 8 operators (twin + `gate_axis_mutate`); compare to a single-trace score vector on the same scenarios |
| **Controls** | 12 handwritten conforming/violating pairs (known separation by construction) |
| **Adapters** | Pin-shaped twins for product realism; not model ranks |
| **Result** | **N/A** until model study |

## RQ2 — Operator information / redundancy

| Field | Protocol |
|-------|----------|
| **Question** | Which operators carry the most information? |
| **Metric** | Marginal contribution to separation; inter-operator redundancy |
| **Harness answer** | Ablate one operator at a time from the full-8 probe set; compare coupling strata (`open_loop` vs `crn_closed_loop`) |
| **Controls** | Same control bank; power floor before scoring models |
| **Result** | **N/A** until model study |

## RQ3 — Predictive validity vs held-out robustness

| Field | Protocol |
|-------|----------|
| **Question** | Do hyperproperty scores predict held-out robustness better than trace scores? |
| **Metric** | Rank correlation (probe score ↔ held-out outcome); baseline = trace-only correlation |
| **Harness answer** | Hold out adversarial scenarios from probe construction; score on training scenarios only |
| **Adapters** | Pins calibrate envelope shape; lab AUROC never accepted as a grade |
| **Result** | **N/A** |

## RQ4 — Paired grading cost / probe-tree amortization

| Field | Protocol |
|-------|----------|
| **Question** | What does paired grading cost, and how much does the probe tree recover? |
| **Metric** | Tick-evaluation counts; protocol amortization factor α (Definition `def:cost` in tex) |
| **Harness answer** | Count ticks under shared-prefix fork vs naive 2n; report α only with measured counts |
| **Non-claim** | Formula alone is **protocol definition**, not a measured result or dollar cost |
| **Result** | **N/A** (definitional α only in this draft) |

## RQ5 — Inconclusive rate vs artifact quality

| Field | Protocol |
|-------|----------|
| **Question** | How often does the comparability check return `inconclusive`, and does that depend on artifact quality? |
| **Metric** | Rate of `inconclusive` with `meta.inconclusive_reason`, stratified by control role / artifact class |
| **Harness answer** | Horizon + shared-prefix / CRN residual checks (`ops/*/spec.yaml` `inconclusive_when`); primary stratum = TRAJSWAP, VARSCALE |
| **Counting rule** | `inconclusive` ≠ green (GATING.md) |
| **Result** | **N/A** |

---

## Results ledger

Pilot values come from `python -m diptych.pilot.study [--task congestion]`
(`diptych/pilot/results.json`, `results_congestion.json`): 80 controllers, 40 per
task — one-shot from four Claude models (20) and OpenAI `gpt-5.6-sol` via Codex (5),
and tool-using agents (Claude Sonnet 5 / Opus 5.5: 10; Codex: 5). Task A is the
concurrency task (`diptych/pilot/TASK.md`, our requirements); Task B is the
congestion task (`diptych/pilot/congestion/TASK.md`, RFC 9743 criteria with our
thresholds). Pilot measurements, not a model comparison.

| RQ | Status | Task A (concurrency) | Task B (congestion) |
|----|--------|----------------------|---------------------|
| RQ1 separation | pilot | 34 pass all basic single-trace checks; 28 fail paired (82%, CI 66–92%); McNemar 33 vs 2 | 16 pass; 3 fail paired (19%, CI 7–43%); McNemar 9 vs 0, p = 0.004 |
| RQ2 which requirements | pilot (descriptive) | trend recency 23 (22 missed by basic), volatility 13 (10), asymmetric response 10 (7), anti-windup 3 (3), setpoint quiet 3 (3) | delay change 14 (2 missed), harm-latency 9 (0), intra-fairness 8 (0), short flows 7 (3), harm-throughput 6 (0), path change 5 (5) |
| RQ3 held-out rank corr. | protocol | N/A | N/A |
| RQ4 α (measured ticks) | pilot | 3.09 (254,076 vs 784,476) | 1.45 (546,000 vs 792,000) |
| RQ5 inconclusive / stability | pilot | 3.1% inconclusive; 88% seed-stable | 0% inconclusive; 93% seed-stable |

Strong baseline: Task A 10 of 11 all-pass controllers fail paired; disagrees with
paired on asymmetric response for 25/40. Task B its Jain's-index reading flags 29
controllers for throughput harm that paired grading passes.

Never back-fill from green×8×3.

See also: [`READINESS.md`](READINESS.md), [`SUBMISSION.md`](SUBMISSION.md),
[`NOTES.md`](NOTES.md), [`ARTIFACT_CHECKLIST.md`](ARTIFACT_CHECKLIST.md).
