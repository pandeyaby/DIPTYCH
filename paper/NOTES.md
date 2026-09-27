# Paper notes

Living source: [`one-trace-is-not-enough.tex`](one-trace-is-not-enough.tex).
Status and pre-upload checklist: [`READINESS.md`](READINESS.md).

## Section map

| § | Content | Backing artifact |
|---|---------|------------------|
| II–IV | Motivating example, property taxonomy, audit of 22 invariants, state-confounding proposition | — |
| V–VI | Harness: coupling, comparability horizon, inconclusive verdicts, probe trees, `gate_axis_mutate`; operator table | `diptych/`, `ops/`, `docs/adapters/` |
| VII | Metrics: probe power, separation index, predictive validity, cost, grader adequacy | — |
| VIII-A–C | Controls, coverage matrix, mutation sweep (Table IV) | `diptych/adequacy.py`, `tests/test_adequacy.py` |
| VIII-D | Pilot on model-written controllers (Table V), measured α | `diptych/pilot/` |
| XI | Threats, including pilot validity | — |

## Open items

- Extend the pilot: tool-using coding agents, models from other vendors, the
  full orchestrator task, held-out scenarios for predictive validity (RQ3).
- Derive operator axis declarations from the graders instead of writing them
  separately (Table IV off-axis column).
- `hist_splice_at` and `frozen` are declared on-axis but never read by their
  graders.
- TRAJSWAP fixtures use a non-binding residual bound (5.0 vs. observed ≤ 0.7).
