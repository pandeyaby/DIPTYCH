# Paper notes

Living source: [`one-trace-is-not-enough.tex`](one-trace-is-not-enough.tex).
Status and pre-upload checklist: [`READINESS.md`](READINESS.md).

## Section map

| § | Content | Backing artifact |
|---|---------|------------------|
| II–IV | Running task, property taxonomy, audit of RFC 9743 (18 behavioral criteria, 8 paired), state-confounding proposition | [`AUDIT_RFC9743.md`](AUDIT_RFC9743.md) |
| V–VI | Harness: coupling, comparability horizon, inconclusive verdicts, probe trees, `gate_axis_mutate`; operator table | `diptych/`, `ops/`, `docs/adapters/` |
| VII | Metrics: probe power, separation index, predictive validity, cost, grader adequacy | — |
| VIII-A–C | Controls, coverage matrix, mutation sweep | `diptych/adequacy.py`, `tests/test_adequacy.py` |
| VIII-D | Pilot on 80 model-written controllers, two tasks (Table IV), measured α | `diptych/pilot/`, `diptych/live.py` |
| XI | Threats, including pilot validity | — |

## Open items

- Extend the pilot: more vendors, larger samples, a packet-level network
  simulator for Task B, held-out scenarios for predictive validity (RQ3).
- Blinded human validation of flagged violations (both authors).
- Derive operator axis declarations from the graders instead of writing them
  separately (§VIII-C).
- `hist_splice_at` and `frozen` are declared on-axis but never read by their
  graders.
- TRAJSWAP fixtures use a non-binding residual bound (5.0 vs. observed ≤ 0.7).
