# IEEE draft outline — DIPTYCH

## Abstract (stub)

We grade agent-authored control systems with paired-trace hyperproperties. DIPTYCH
couples executions that share a prefix, applies one controlled operator axis, and
requires asymmetric verdicts plus power-on-axis. Eight operators cover open-loop and
CRN closed-loop regimes. Product adapters emit probe twins; the harness grades.

## I. Introduction

- Single-run demos overclaim calibration
- 2-safety: bad thing is a *pair*
- Film metaphor: one reel legal; calibration is the diptych

## II. Background

- Hyperproperties / k-safety
- CRN closed-loop coupling
- Related: relational verification, RV for hyperproperties

## III. DIPTYCH model

- Shared prefix, suffix probes, incomparability discard
- Schema 0.2 envelope
- Verdicts: pass / fail / inconclusive (inconclusive ≠ green)

## IV. Operators

| Wave | Ops |
|------|-----|
| A | FREEZEDRY, RESEED, SCHEMAX |
| B | SIGNFLIP, SATEXTEND, HISTSWAP |
| C (CRN) | TRAJSWAP, VARSCALE |

## V. Gates

- Manifest (8×{conforming,violating})
- Contrast (asymmetric expected verdicts)
- `gate_axis_mutate` power-on-axis
- Stub rejection (TODO / hardcoded pass / empty traces)

## VI. Adapters & pins

- ZeroDay@fb5b39da
- AOMB@667e475

## VII. Evaluation

- Offline PoC (`./scripts/run_poc.sh`)
- Coverage matrix cells

## VIII. Limitations

- No exploitability claims
- No AUROC as hyperproperty grade

## IX. Conclusion
