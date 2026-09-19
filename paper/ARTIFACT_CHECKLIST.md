# IEEE artifact checklist — DIPTYCH

Living paper: [`one-trace-is-not-enough.tex`](one-trace-is-not-enough.tex)  
Repo: https://github.com/pandeyaby/DIPTYCH · schema `0.2`

This checklist is for reviewers and for keeping the IEEEtran draft honest.
It lists **what ships**, **how to reproduce**, and **what we refuse to claim**.

## 1. Code (harness only)

| Item | Path |
|------|------|
| Core package (contract, grade, gates, `gate_axis_mutate`) | `diptych/` |
| Operator specs + implementations | `ops/<OP>/spec.yaml`, `ops/<OP>/operator.py` |
| Full-8 fixture twins (`source=diptych_core`) | `diptych-probes/<OP>/{conforming,violating}/` |
| Adapter pins (read-only SHAs) | `adapters/PINS.md` |
| CI (pytest + PoC + PDF build) | `.github/workflows/ci.yml` |

**Out of scope for this artifact:** ZeroDay / AOMB *product* trees are **not**
vendored or edited here. They emit schema-`0.2` twins at pinned SHAs; DIPTYCH grades.

## 2. Handwritten controls

| Item | Path |
|------|------|
| Conforming / violating controls per operator | `controls/<OP>/{conforming,violating}.py` |
| Operator semantics | `docs/OPERATORS.md`, `docs/adapters/OPERATOR_TABLE.md` |
| Gating / thin-impl rejection | `docs/adapters/GATING.md`, `docs/adapters/CONTRACT.md` |

Controls establish **probe power** before any model is scored (paper §VII).

## 3. Coverage matrix + logs

| Item | Path / command |
|------|----------------|
| Live matrix (authoritative cells) | `coverage/matrix.json` |
| Expected PoC snippet (green×8×3) | `examples/poc/expected_matrix_snippet.json` |
| Refresh matrix + full-8 gate + matrix check | `./scripts/run_poc.sh` (or `make poc`) |
| Print matrix only | `make matrix` |
| Unit tests | `PYTHONPATH=. python -m pytest -q` (or `make test`) |

**Green rule (same as paper Table `tab:coverage`):** conforming→`pass`,
violating→`fail`, and `gate_axis_mutate` reports axis power.
`inconclusive` ≠ green. Cells are categorical + boolean `axis_power` only.

### Pins cited by the paper / matrix notes

| Adapter | Short | Full SHA |
|---------|-------|----------|
| ZeroDay | `fb5b39da` | `fb5b39daf88e37521aaee8526ae9d286cf74f341` |
| AOMB | `667e475` | `667e47538ae5b9c504187b7a73220d22aa8fb96f` |

Source of truth: `adapters/PINS.md` (mirrored in root `README.md`).
Record the git commit you graded against (paper cites these shorts; full SHAs above).

### Reproduce offline (no GPU, no network for core PoC)

```bash
git clone https://github.com/pandeyaby/DIPTYCH.git
cd DIPTYCH
# Python ≥ 3.10; stdlib-only core
./scripts/run_poc.sh
# expect exit 0; refreshes coverage/matrix.json; prints green×8×3; MATRIX CHECK OK
PYTHONPATH=. python -m pytest -q   # optional CI parity (pip install pytest)
```

Exit `0` means full-8 cells are green on `diptych_core` / `zeroday` / `aomb`
(twin contrast + axis power) and the live matrix matches
`examples/poc/expected_matrix_snippet.json`. Adapter columns are attributed to
the pins above—never invented model scores.

### What green×8×3 does and does **not** claim

| Does claim | Does **not** claim |
|------------|--------------------|
| Every operator has conforming→pass / violating→fail twins | Vulnerability finding / exploit success |
| `gate_axis_mutate` flips pass→fail on that axis | Accuracy, AUROC, F1, or model ranking |
| Adapter columns green at the pinned SHAs above | That product trees are vendored here |
| Protocol / QC readiness of the grader | That the model study is complete |

## 4. Paper + figures

| Item | Path |
|------|------|
| Authoritative IEEEtran | `paper/one-trace-is-not-enough.tex` |
| Bibliography (BibTeX) | `paper/refs.bib` |
| Compile notes | `paper/README.md` |
| Open items | `paper/NOTES.md` |
| Diagram: one film vs diptych | `docs/images/diptych-vs-single-trace.{png,svg}` |
| Diagram: adapters → graders | `docs/images/stack.{png,svg}` |
| Markdown prose draft | `drafts/ieee-draft.md` |
| Outline | `PAPER_OUTLINE.md`, `drafts/ieee-outline.md` |

PNG files are what `\includegraphics` and the root README use. SVG files are the
editable sources (keep text glyphs in sync with PNGs).

Compile (from `paper/`, TeX with `IEEEtran.cls` + BibTeX):

```bash
latexmk -pdf -interaction=nonstopmode -halt-on-error one-trace-is-not-enough.tex
```

Figures resolve via `\graphicspath` to `../docs/images/`.

### Download the CI PDF artifact

Job **Build paper PDF** uploads `one-trace-is-not-enough.pdf` as workflow
artifact **`one-trace-is-not-enough-pdf`**.

**GitHub UI**

1. Open https://github.com/pandeyaby/DIPTYCH/actions
2. Select a green run on `main` or your PR
3. Job **Build paper PDF** → Artifacts → **`one-trace-is-not-enough-pdf`**
4. Download and unzip → `one-trace-is-not-enough.pdf`

**CLI (optional)**

```bash
gh run list --workflow=ci.yml --branch main --limit 5
gh run download <RUN_ID> -n one-trace-is-not-enough-pdf
```

## 5. Non-claims (must match GATING + paper §threats)

- **No fabricated LLM / model scores.** Table of model-study power / separation
  stays blank (`---` in the `.tex`) until the study completes.
- **No AUROC / `model_grade` fields** in graded envelopes (contract reject).
- **No exploit / attack / PoC payloads.** Localization ≠ exploitability.
- **`inconclusive` ≠ green.**
- **Protocol + handwritten controls first**; model study is in progress.
- **Pins only** for ZeroDay / AOMB; no product-tree edits in this repo.
- **green×8×3** = harness coverage + axis power (`gate_axis_mutate` +
  conforming/violating controls) — **not** vulnerability-finding or accuracy.

## 6. Reviewer smoke path

1. Read abstract + §VII (evaluation **protocol**, not results).
2. Confirm Table `tab:coverage` matches `coverage/matrix.json`.
3. Confirm pins match `adapters/PINS.md` / README.
4. Run `./scripts/run_poc.sh` → exit 0 + `MATRIX CHECK OK`.
5. Confirm Table `tab:placeholder` has no numeric model scores.
6. (Optional) Download CI PDF artifact `one-trace-is-not-enough-pdf`.
