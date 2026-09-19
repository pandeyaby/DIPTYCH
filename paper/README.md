# Paper — DIPTYCH (IEEEtran)

**Title:** *One Trace Is Not Enough: Hyperproperty Grading for Agent-Authored Control Systems*

**Living source:** [`one-trace-is-not-enough.tex`](one-trace-is-not-enough.tex) (IEEEtran `conference`)

**Authors:** Abhishek Pandey (Meta), Abhinav Pandey (Cisco)

## Compile

```bash
pdflatex one-trace-is-not-enough.tex
```

Requires a TeX distribution with `IEEEtran.cls`.

## Section map

| § | Content |
|---|--------|
| I | Introduction |
| II | Related work / hyperproperty taxonomy |
| III | DIPTYCH model |
| IV | Eight operators |
| V | Gates + `gate_axis_mutate` |
| VI | Adapters: ZeroDay@`fb5b39da`, AOMB@`667e475` |
| **VII** | **Evaluation protocol** (no invented AUROC / model scores) |
| VIII | Limitations / ethics / non-claims |
| IX | Conclusion |

## Provenance

Synthesized from [`../drafts/ieee-draft.md`](../drafts/ieee-draft.md) and [`../PAPER_OUTLINE.md`](../PAPER_OUTLINE.md) when no author PDF binary was available.
Markdown drafts remain under `drafts/` for prose iteration; this directory is the conference-ready continuous source.
