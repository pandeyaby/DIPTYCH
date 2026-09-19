# Paper — DIPTYCH (IEEEtran)

**Title:** *One Trace Is Not Enough: Hyperproperty Grading for Agent-Authored Control Systems*

**Living source:** [`one-trace-is-not-enough.tex`](one-trace-is-not-enough.tex) (IEEEtran `conference`)

**Authors:** Abhishek Pandey (Meta), Abhinav Pandey (Cisco)

**Artifact checklist:** [`ARTIFACT_CHECKLIST.md`](ARTIFACT_CHECKLIST.md)  
**Open items:** [`NOTES.md`](NOTES.md)

## Compile

```bash
cd paper
pdflatex one-trace-is-not-enough.tex
```

Requires a TeX distribution with `IEEEtran.cls`. Figures resolve via
`\graphicspath` to `../docs/images/`.

## Section map (authoritative)

| § | Label | Content |
|---|--------|--------|
| I | — | Introduction |
| II | `sec:motivating` | Motivating example + Fig. diptych |
| III | `sec:taxonomy` | Property taxonomy |
| IV | `sec:audit` | Spec audit + Proposition 1 |
| V | `sec:harness` | Diptych harness + `gate_axis_mutate` |
| VI | `sec:operators` | Eight operators (coupling = harness enum) |
| — | `sec:metrics` | Metrics (definitions only) |
| **VII** | **`sec:protocol`** | **Evaluation protocol** + pins + Table `tab:coverage`; **no model scores** |
| VIII | `sec:related` | Related work |
| IX | `sec:threats` | Threats + non-claims (GATING-aligned) |
| X | — | Conclusion |

## Figures (repo paths)

| Caption role | Path |
|--------------|------|
| One film vs coupled diptych | `docs/images/diptych-vs-single-trace.png` |
| Adapters feed DIPTYCH graders | `docs/images/stack.png` |

Do **not** invent new results figures; these are architecture / protocol diagrams only.

## Provenance

Authoritative author IEEEtran source, kept consistent with:

- ZeroDay@`fb5b39da` / AOMB@`667e475` adapter placement (`adapters/PINS.md`)
- Offline green×8×3 + `gate_axis_mutate` facts from `coverage/matrix.json`
- Explicit non-claims (no invented AUROC / model scores; protocol + controls first)

Markdown drafts under `drafts/` remain for prose iteration; they must not contradict the `.tex`.
