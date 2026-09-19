# IEEE submission package — DIPTYCH

Camera-ready / artifact packaging notes for the living IEEEtran source
[`one-trace-is-not-enough.tex`](one-trace-is-not-enough.tex).

**Corresponding author:** Abhinav Pandey · `pandey.aby@gmail.com`  
**Co-author:** Abhishek Pandey (Meta) · `pandeyabhi1987@gmail.com`

This file is harness documentation only. It does **not** invent AUROC, accuracy,
or model-study scores. Product trees (ZeroDay / AOMB) are **not** edited here.

---

## 1. Target venue (placeholder)

| Field | Value |
|-------|--------|
| Venue | **TBD — IEEE conference** (IEEEtran `conference` class already used) |
| Track / topic | Agentic evaluation, hyperproperties, metamorphic / paired-trace grading |
| Anonymization | See §5; decide double-blind vs camera-ready before upload |
| Corresponding author | **Abhinav Pandey** (`pandey.aby@gmail.com`) |

Replace “TBD” with the concrete CFP (name, year, page limit, artifact option)
before camera-ready. Do not change claims to chase a venue metric.

---

## 2. Page budget (IEEEtran conference)

Typical IEEE conference limits (confirm against the chosen CFP):

| Item | Usual budget | DIPTYCH note |
|------|--------------|--------------|
| Main body | **6 pages** (+1 refs often allowed) | Living draft; trim related-work / protocol prose first if over |
| References | Often excluded or +1 page | `\bibliographystyle{IEEEtran}` + `refs.bib` |
| Figures | Count toward body | Two architecture figures only (`diptych-vs-single-trace`, `stack`) |
| Tables | Count toward body | `tab:ops`, `tab:coverage`, blank `tab:placeholder`, audit/taxonomy |

**Trim order if over length (do not invent results):** (1) related-work density,
(2) protocol command blocks → cite `ARTIFACT_CHECKLIST.md`, (3) duplicate
pin/SHA prose, (4) never fill `tab:placeholder` with placeholder numbers.

**Build PDF locally:** `make paper` (requires `latexmk` + TeX Live with
`IEEEtran`). **CI fallback:** job `paper-pdf` in `.github/workflows/ci.yml`
uploads `one-trace-is-not-enough-pdf` when local TeX is absent.

---

## 3. Artifact zip (what to include)

**Preferred (automated):** from repository root

```bash
make artifact          # or: ./scripts/pack_artifact.sh
# writes dist/DIPTYCH-<shortsha>.zip
```

The pack script records `CODE_SHA.txt`, copies `coverage/matrix.json`, the PoC
expected snippet, pins, key paper docs (`SUBMISSION.md`, `ARTIFACT_CHECKLIST.md`,
`RQ_PROTOCOL.md`, `ANON.md`, `.tex` / `refs.bib`), harness trees needed for
`./scripts/run_poc.sh`, and writes `ARTIFACT_NOTES.txt`.

**PDF attachment:** if `paper/one-trace-is-not-enough.pdf` exists (from
`make paper`), it is bundled. Otherwise `ARTIFACT_NOTES.txt` explains how to
download CI workflow artifact **`one-trace-is-not-enough-pdf`** from Actions and
place the PDF under `paper/` (or submit alongside the zip).

Package a reviewer-facing zip (or GitHub release tarball) that reproduces
**green×8×3** without GPU or network for the core PoC.

| Include | Path / how |
|---------|------------|
| Code SHA | `CODE_SHA.txt` via `scripts/pack_artifact.sh` / annotated tag |
| Harness tree | `diptych/`, `ops/`, `controls/`, `diptych-probes/`, `scripts/`, `tests/` |
| Pins | `adapters/PINS.md` (ZeroDay@`fb5b39da`, AOMB@`667e475`) |
| Live matrix | `coverage/matrix.json` (refresh with `./scripts/run_poc.sh`) |
| Expected snippet | `examples/poc/expected_matrix_snippet.json` |
| PoC notes | `ARTIFACT_NOTES.txt` + optional `artifact/matrix_snapshot.txt` |
| Paper PDF | `paper/one-trace-is-not-enough.pdf` (from `make paper` or CI artifact) |
| Paper source | `paper/*.tex`, `paper/refs.bib`, `docs/images/*` |
| RQ / anon docs | `paper/RQ_PROTOCOL.md`, `paper/ANON.md` |
| Checklists | `paper/ARTIFACT_CHECKLIST.md`, this file (`SUBMISSION.md`) |
| License | `LICENSE` |

| Exclude | Why |
|---------|-----|
| ZeroDay / AOMB product trees | Not vendored; pins only |
| Exploit / attack payloads | Contract forbid |
| Invented model scores / AUROC tables | Non-claim; `tab:placeholder` stays `---` |
| Secrets, tokens, private emails beyond author block | Privacy |

### Suggested zip layout

```
DIPTYCH-<shortsha>/
  README.md                 # point to stranger path + non-claims
  CODE_SHA.txt              # full git SHA
  ARTIFACT_NOTES.txt        # CI PDF attach instructions
  coverage/matrix.json
  artifact/matrix_snapshot.txt
  paper/one-trace-is-not-enough.pdf   # optional; else attach from CI
  paper/SUBMISSION.md
  paper/RQ_PROTOCOL.md
  paper/ANON.md
  paper/ARTIFACT_CHECKLIST.md
  <repo tree as needed for PoC>
```

### Reproduce from zip

```bash
cd DIPTYCH-<shortsha>
./scripts/run_poc.sh    # expect exit 0; MATRIX CHECK OK
# optional CI-parity:
pip install pytest && PYTHONPATH=. python -m pytest -q
```

---

## 4. What “done” means for submission (honest)

- [ ] PDF builds (`make paper` or CI `paper-pdf`)
- [ ] `./scripts/run_poc.sh` exit 0; matrix matches snippet
- [ ] `make artifact` / `./scripts/pack_artifact.sh` produces zip with CODE_SHA + matrix
- [ ] Pins in PDF / checklist match `adapters/PINS.md`
- [ ] Table `tab:ops` coupling + graded channels match
      `docs/adapters/OPERATOR_TABLE.md` / `ops/*/spec.yaml`
- [ ] Table `tab:placeholder` has **no** numeric model scores
- [ ] RQ1–RQ5 result cells stay N/A (`paper/RQ_PROTOCOL.md`)
- [ ] Threats § lists concrete DIPTYCH limits (pins, inconclusive, controls, coupling)
- [ ] Venue name + page limit filled in this file
- [ ] Anonymization path ready if needed (`paper/ANON.md`)
- [ ] Abhinav approves merge / camera-ready (HOLD for yes)

---

## 5. Anonymization notes (if double-blind)

**Authoritative switch:** [`ANON.md`](ANON.md) (commented author block in the
`.tex` + deanonymizing-string checklist). Venue remains TBD — do not invent one.

If the venue requires anonymity:

1. Replace author block with paper ID / “Anonymous Submission” (see `ANON.md`).
2. Redact GitHub URLs that deanonymize (`pandeyaby/DIPTYCH` → “supplementary
   anonymous repo” or CMT upload). Prefer an anonymized mirror or zip-only
   artifact for review.
3. Drop personal emails from the PDF; keep corresponding-author contact in the
   CMT form only.
4. Adapter pin SHAs may remain (they identify *product* commits, not authors);
   do not add self-citation that reveals identity.
5. Camera-ready: restore authors — **corresponding author Abhinav Pandey**
   (`pandey.aby@gmail.com`).

If the venue is single-blind / open review, skip steps 1–4 and keep the living
author block in the `.tex`.

---

## 6. Non-claims (must survive camera-ready)

- No fabricated LLM / model scores; blank `tab:placeholder` until the study ends.
- No AUROC / `model_grade` in graded envelopes.
- No exploit payloads; localization ≠ exploitability.
- `inconclusive` ≠ green.
- green×8×3 = harness coverage + `gate_axis_mutate` only.
- ZeroDay / AOMB are **pins**, not in-repo product edits.

See also: [`ARTIFACT_CHECKLIST.md`](ARTIFACT_CHECKLIST.md), [`NOTES.md`](NOTES.md),
[`RQ_PROTOCOL.md`](RQ_PROTOCOL.md), [`ANON.md`](ANON.md),
[`docs/adapters/GATING.md`](../docs/adapters/GATING.md).
