# Witness / adapter appendix (paper companion)

Short reviewer note for the living IEEEtran source
[`one-trace-is-not-enough.tex`](one-trace-is-not-enough.tex)
(Appendix / `sec:witnesses`).

**Canonical recipes:** [`docs/adapters/WITNESSES.md`](../docs/adapters/WITNESSES.md)  
**Gating / green rule:** [`docs/adapters/GATING.md`](../docs/adapters/GATING.md)  
**Pins:** [`adapters/PINS.md`](../adapters/PINS.md)  
**RQ result cells (all N/A):** [`RQ_PROTOCOL.md`](RQ_PROTOCOL.md)

## What to audit

| Claim in PDF | Where to verify |
|--------------|-----------------|
| green×8×3 coverage | `coverage/matrix.json` + `./scripts/run_poc.sh` |
| Axis power | `diptych.gates.gate_axis_mutate` + WITNESSES recipes |
| Adapter columns | Pins only (ZeroDay@`fb5b39da`, AOMB@`667e475`) — trees not vendored |
| RQ1–RQ5 scores | **N/A** — protocol scaffolding only |

## Non-claims (repeat for camera-ready)

- Witness recipes prove **probe / axis power**, not vulnerability finding.
- No AUROC, accuracy, F1, or model ranks.
- `inconclusive` ≠ green.
- No ZeroDay / AOMB product-tree edits in this harness repo.
