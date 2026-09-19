# Paper — DIPTYCH (IEEEtran)

**Title:** *One Trace Is Not Enough: Hyperproperty Grading for Agent-Authored Control Systems*

**Living source:** [`one-trace-is-not-enough.tex`](one-trace-is-not-enough.tex) (IEEEtran `conference`)

**Authors:** Abhishek Pandey (Meta), Abhinav Pandey (Cisco)

## Compile

```bash
pdflatex one-trace-is-not-enough.tex
```

Requires a TeX distribution with `IEEEtran.cls`.

## Section map (authoritative)

| § | Label | Content |
|---|--------|--------|
| I | — | Introduction |
| II | `sec:motivating` | Motivating example |
| III | `sec:taxonomy` | Property taxonomy |
| IV | `sec:audit` | Spec audit + Proposition 1 |
| V | `sec:harness` | Diptych harness + `gate_axis_mutate` |
| VI | `sec:operators` | Eight operators |
| — | `sec:metrics` | Metrics (definitions only) |
| **VII** | **`sec:protocol`** | **Evaluation protocol** + ZeroDay/AOMB pins; **no model scores** |
| VIII | `sec:related` | Related work |
| IX | `sec:threats` | Threats to validity |
| X | — | Conclusion |

## Provenance

Authoritative author IEEEtran source (uploaded), extended only for:

- ZeroDay@`fb5b39da` / AOMB@`667e475` adapter placement
- Offline green×8 + `gate_axis_mutate` protocol facts from `coverage/matrix.json`
- Explicit non-claims (no invented AUROC / model scores)

Markdown drafts under `drafts/` remain for prose iteration.
