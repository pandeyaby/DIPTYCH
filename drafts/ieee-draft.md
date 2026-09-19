# One Trace Is Not Enough — markdown stub (non-authoritative)

**Living IEEEtran (source of truth):**
[`paper/one-trace-is-not-enough.tex`](../paper/one-trace-is-not-enough.tex)

**Authors:** Abhinav Pandey, Abhishek Pandey (Meta)  
**Artifact:** https://github.com/pandeyaby/DIPTYCH · schema `0.2`  
**Pins:** ZeroDay@`fb5b39da` · AOMB@`667e475` (`adapters/PINS.md`)  
**RQ ledger:** [`paper/RQ_PROTOCOL.md`](../paper/RQ_PROTOCOL.md) (all result cells **N/A**)  
**Witnesses:** [`paper/WITNESSES.md`](../paper/WITNESSES.md) ·
[`docs/adapters/WITNESSES.md`](../docs/adapters/WITNESSES.md)

This file is a **page-budget stub**. Prose that diverged from the `.tex` was
removed; expand only in the IEEEtran source.

---

## One-paragraph abstract (pointer)

Agentic benchmarks grade one trace at a time; calibration-style requirements are
$2$-safety hyperproperties. **DIPTYCH** grades coupled twins under schema
`0.2` with eight operators and `gate_axis_mutate`. Offline evaluation reports
**green×8×3** harness coverage + axis power at the cited pins. Section VII is an
evaluation **protocol**; the model study is in progress—**no AUROC / model
scores**.

## Claims vs non-claims

| Proves | Does not prove |
|--------|----------------|
| Twin contrast + axis power (coverage matrix) | Vulnerability finding / exploitability |
| Grader QC before a model study | Accuracy, AUROC, F1, model ranks |
| Pin-scoped adapter greens | Floating `main` product quality |

RQ1–RQ5 result cells stay **N/A** until a real model study
(`paper/RQ_PROTOCOL.md`). `inconclusive` ≠ green. No product-tree edits here.

## Reproduce

```bash
./scripts/run_poc.sh          # exit 0 ⇒ green×8×3
make artifact                 # reviewer zip (CI dry-runs this)
make paper                    # or CI job paper-pdf
```

Figures: `docs/images/diptych-vs-single-trace.{svg,png}`,
`docs/images/stack.{svg,png}`. Checklist:
[`paper/ARTIFACT_CHECKLIST.md`](../paper/ARTIFACT_CHECKLIST.md).
