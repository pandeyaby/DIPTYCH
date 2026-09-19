# IEEE draft outline — DIPTYCH

**Expanded draft:** [`drafts/ieee-draft.md`](ieee-draft.md)  
**Authors:** Abhinav Pandey, Abhishek Pandey (affiliation placeholders)

## Abstract (stub → expanded in ieee-draft.md)

We grade agent-authored control systems with paired-trace hyperproperties. DIPTYCH
couples executions that share a prefix, applies one controlled operator axis, and
requires asymmetric verdicts plus power-on-axis. Eight operators cover open-loop and
CRN closed-loop regimes. Product adapters emit probe twins; the harness grades.

## I. Introduction

- Single-run demos overclaim calibration
- 2-safety: bad thing is a *pair*
- Film metaphor: one reel legal; calibration is the diptych

## II. Background / related work / hyperproperty taxonomy

- Hyperproperties / k-safety
- CRN closed-loop coupling
- Related: relational verification, RV for hyperproperties
- Taxonomy table (trace vs 2-safety vs power-on-axis)

## III. DIPTYCH model

- Shared prefix, suffix probes, incomparability discard
- Schema 0.2 envelope
- Verdicts: pass / fail / inconclusive (inconclusive ≠ green)
- Harness + `gate_axis_mutate`

## IV. Operators (8-operator table)

| Wave | Ops |
|------|-----|
| A | FREEZEDRY, RESEED, SCHEMAX |
| B | SIGNFLIP, SATEXTEND, HISTSWAP |
| C (CRN) | TRAJSWAP, VARSCALE |

## V. Gates / methods

- Manifest (8×{conforming,violating})
- Contrast (asymmetric expected verdicts)
- `gate_axis_mutate` power-on-axis
- Stub rejection (TODO / hardcoded pass / empty traces)
- Offline PoC: `./scripts/run_poc.sh`

## VI. Adapters & pins

- ZeroDay@`fb5b39daf88e37521aaee8526ae9d286cf74f341` (fb5b39da)
- AOMB@`667e47538ae5b9c504187b7a73220d22aa8fb96f` (667e475)
- See `adapters/PINS.md` (read-only; no product-tree edits)

## VII. Evaluation

- Offline PoC (`./scripts/run_poc.sh`)
- Live coverage matrix from `coverage/matrix.json`
- Cite only repo/CI facts — no AUROC, no invented model scores

## VIII. Threats to validity / limitations

- No exploitability claims
- No AUROC as hyperproperty grade
- Adapter columns pending until emitters pass

## IX. Conclusion

See full prose in [`ieee-draft.md`](ieee-draft.md).
