# Paper — DIPTYCH (IEEEtran)

**Title:** *One Trace Is Not Enough: Hyperproperty Grading for Agent-Authored Control Systems*

**Living source:** [`one-trace-is-not-enough.tex`](one-trace-is-not-enough.tex) (IEEEtran `conference`)  
**Bibliography:** [`refs.bib`](refs.bib) (BibTeX / `IEEEtran.bst`)

**Authors:** Abhinav Pandey, Abhishek Pandey (Meta)

**Artifact checklist:** [`ARTIFACT_CHECKLIST.md`](ARTIFACT_CHECKLIST.md)  
**Open items:** [`NOTES.md`](NOTES.md)

## Compile locally

Requires a TeX distribution with `IEEEtran.cls`, `IEEEtran.bst`, and `latexmk`
(Debian/Ubuntu: `texlive-publishers`, `texlive-latex-extra`, `latexmk`).

```bash
cd paper
latexmk -pdf -interaction=nonstopmode -halt-on-error one-trace-is-not-enough.tex
```

That runs `pdflatex` → `bibtex` → `pdflatex` ×2 as needed and writes
`one-trace-is-not-enough.pdf`. Figures resolve via `\graphicspath` to
`../docs/images/`.

Manual equivalent:

```bash
cd paper
pdflatex -interaction=nonstopmode -halt-on-error one-trace-is-not-enough.tex
bibtex one-trace-is-not-enough
pdflatex -interaction=nonstopmode -halt-on-error one-trace-is-not-enough.tex
pdflatex -interaction=nonstopmode -halt-on-error one-trace-is-not-enough.tex
```

## CI PDF artifact (download)

GitHub Actions job **Build paper PDF** (`.github/workflows/ci.yml`) installs the
same TeX packages, builds the PDF, and uploads workflow artifact
**`one-trace-is-not-enough-pdf`**.

1. https://github.com/pandeyaby/DIPTYCH/actions → pick a green run
2. Artifacts → **`one-trace-is-not-enough-pdf`** → download zip
3. Or: `gh run download <RUN_ID> -n one-trace-is-not-enough-pdf`

## Reproduce the coverage matrix (pin SHAs)

From repository root (Python ≥ 3.10, stdlib-only):

```bash
./scripts/run_poc.sh    # exit 0 → GATE PASS + MATRIX CHECK OK
make matrix             # print green×8×3 table only
```

Live cells: `coverage/matrix.json`. Expected stranger contract:
`examples/poc/expected_matrix_snippet.json`.

| Adapter | Short | Full SHA |
|---------|-------|----------|
| ZeroDay | `fb5b39da` | `fb5b39daf88e37521aaee8526ae9d286cf74f341` |
| AOMB | `667e475` | `667e47538ae5b9c504187b7a73220d22aa8fb96f` |

**green×8×3** = twin contrast + `gate_axis_mutate` axis power across
`diptych_core` × `zeroday` × `aomb` for eight operators. It is **not** an
accuracy, AUROC, or vulnerability-finding claim. Model-study Table
`tab:placeholder` stays blank (`---`) until real data exists.

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
| **VII** | **`sec:protocol`** | **Evaluation protocol** + ZeroDay/AOMB case studies + Table `tab:coverage`; **no model scores** |
| VIII | `sec:related` | Related work (real BibTeX citations) |
| IX | `sec:threats` | Threats + non-claims (GATING-aligned) |
| X | — | Conclusion |

## Figures (repo paths)

| Caption role | Path (PNG used by tex/README) | Editable source |
|--------------|-------------------------------|-----------------|
| One film vs coupled diptych | `docs/images/diptych-vs-single-trace.png` | `.svg` sibling |
| Adapters feed DIPTYCH graders | `docs/images/stack.png` | `.svg` sibling |

Do **not** invent new results figures; these are architecture / protocol diagrams only.
Captions must not imply model scores or exploit findings.

## Provenance

Authoritative author IEEEtran source, kept consistent with:

- ZeroDay@`fb5b39da` / AOMB@`667e475` adapter placement (`adapters/PINS.md`)
- Offline green×8×3 = harness coverage + `gate_axis_mutate` axis power from `coverage/matrix.json` (not vulnerability / accuracy claims)
- Explicit non-claims (no invented AUROC / F1 / model scores; protocol + controls first)

Markdown drafts under `drafts/` remain for prose iteration; they must not contradict the `.tex`.
