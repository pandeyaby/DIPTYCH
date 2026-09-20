# IEEE submission package — DIPTYCH

Camera-ready / artifact packaging notes for the living IEEEtran source
[`one-trace-is-not-enough.tex`](one-trace-is-not-enough.tex).

**Corresponding author:** Abhinav Pandey · `pandey.aby@gmail.com`  
**Co-author:** Abhishek Pandey (Meta) · `pandeyabhi1987@gmail.com`

This file is harness documentation only. It does **not** invent AUROC, accuracy,
or model-study scores. Product trees (ZeroDay / AOMB) are **not** edited here.

**HOLD for Abhinav merge yes** before treating any upload as submission-final.

**One-page scorecard:** [`READINESS.md`](READINESS.md) (green vs human TODOs;
as of main `4576506a` after #11). **Milestone log:** root
[`CHANGELOG.md`](../CHANGELOG.md). **License:** root [`LICENSE`](../LICENSE)
(MIT) — required in artifact zip.

---

## 0. Venue fill-in (TODOs — do not invent)

Replace every `TODO` / `TBD` below with the concrete CFP before camera-ready.
Do **not** invent a venue name, deadline, or page limit. Do **not** change
claims to chase a venue metric.

| Field | Value (fill before upload) |
|-------|----------------------------|
| **Venue name** | `TODO — IEEE conference / workshop name` |
| **Venue year / edition** | `TODO — e.g. 2027` |
| **CFP / submission deadline** | `TODO — YYYY-MM-DD (AoE/local as CFP states)` |
| **Page limit (main body)** | `TODO — confirm CFP (typical IEEE conf: 6pp; some allow 8)` |
| **References policy** | `TODO — included in limit / +1 page / unlimited?` |
| **Artifact / code option** | `TODO — required / optional / badge track?` |
| **Anonymization** | `TODO — double-blind vs single-blind / open` → see [`ANON.md`](ANON.md) |
| **Track / topic** | Agentic evaluation, hyperproperties, metamorphic / paired-trace grading |
| **Corresponding author (CMT/HotCRP)** | **Abhinav Pandey** · `pandey.aby@gmail.com` |
| **Corresponding affiliation** | `TODO — Independent / affiliation line for camera-ready` |
| **Co-author contact (camera-ready only)** | Abhishek Pandey (Meta) · `pandeyabhi1987@gmail.com` |

**Checklist before upload**

- [ ] Venue name filled (not TBD)
- [ ] Deadline recorded
- [ ] Page limit recorded and compared to measured PDF count (§2)
- [ ] Corresponding-author contact confirmed in CMT/HotCRP
- [ ] Anonymization path applied if double-blind (`ANON.md`)
- [ ] Artifact zip inventory matches `make artifact` (§3)
- [ ] Abhinav merge / camera-ready yes

---

## 1. Target venue (summary)

| Field | Value |
|-------|--------|
| Venue | **TBD — IEEE conference** (IEEEtran `conference` class already used) |
| Track / topic | Agentic evaluation, hyperproperties, metamorphic / paired-trace grading |
| Anonymization | See §5 + [`ANON.md`](ANON.md); decide double-blind vs camera-ready before upload |
| Corresponding author | **Abhinav Pandey** (`pandey.aby@gmail.com`) |

---

## 2. Page budget (IEEEtran conference)

### Measured page count (record after every latexmk / CI `paper-pdf`)

| Build | Pages | Notes |
|-------|------:|-------|
| Local `make paper` / latexmk (this polish draft) | **8** | Body+appendix+refs fit on 8pp after abstract/related-work/pin trim |
| Prior living draft (#8 era) | ~10 | Trimmed related-work / pin prose / blank `tab:placeholder` rows |
| Venue limit | **TODO** (§0) | Compare after CFP is chosen |

**Interpretation vs typical IEEE budgets**

| Typical CFP | DIPTYCH note |
|-------------|--------------|
| 6 pages (+1 refs often) | Still over for strict 6pp inclusive; further trim only after §0 page limit is known — do not invent results |
| 8 pages (+ refs / inclusive) | **Living draft fits 8pp** (body+appendix+refs) |
| Custom workshop limit | Re-measure after `make paper`; update this table |

Typical IEEE conference limits (confirm against the chosen CFP):

| Item | Usual budget | DIPTYCH note |
|------|--------------|--------------|
| Main body | **6–8 pages** | Living draft **8pp** total (abstract trim, related-work density, pin prose, table width); Discussion owns green×8×3 vs RQ N/A |
| References | Often excluded or +1 page | `\bibliographystyle{IEEEtran}` + `refs.bib` |
| Figures | Count toward body | Two architecture figures only (`diptych-vs-single-trace`, `stack`) |
| Tables | Count toward body | `tab:ops`, `tab:coverage`, reserved blank `tab:placeholder`, audit/taxonomy |
| Appendix | Often counts toward body | Short witness pointer (`sec:witnesses`); recipes live in `docs/adapters/WITNESSES.md` |

**Trim order if over length (do not invent results):** (1) related-work density,
(2) duplicate pin/SHA prose (Discussion already states the green rule),
(3) protocol command blocks → cite `ARTIFACT_CHECKLIST.md`,
(4) never fill `tab:placeholder` with placeholder numbers.
**Markdown drafts** under `drafts/` are stubs; do not re-expand them past the `.tex`.

**Build PDF locally:** `make paper` (requires `latexmk` + TeX Live with
`IEEEtran`). **CI fallback:** job `paper-pdf` in `.github/workflows/ci.yml`
uploads `one-trace-is-not-enough-pdf` when local TeX is absent.

After any build, update the measured-count row above and
[`NOTES.md`](NOTES.md).

---

## 3. Artifact zip (inventory = `make artifact` output)

**Preferred (automated):** from repository root

```bash
make artifact          # or: ./scripts/pack_artifact.sh
# writes dist/DIPTYCH-<shortsha>.zip   (or .tar.gz if zip unavailable)
```

### Exact inventory (matches `scripts/pack_artifact.sh`)

Paths below are relative to the bundle root `DIPTYCH-<shortsha>/`.

| Member | Path | Required? |
|--------|------|-----------|
| Code SHA | `CODE_SHA.txt` (`short=` / `full=`) | **yes** |
| Pack notes | `ARTIFACT_NOTES.txt` (CI PDF attach + non-claims) | **yes** |
| Citation | `CITATION.cff` (pandeyaby/DIPTYCH; no DOI) | **yes** |
| Live matrix | `coverage/matrix.json` | **yes** |
| Expected PoC snippet | `examples/poc/expected_matrix_snippet.json` | **yes** |
| Pins | `adapters/PINS.md` | **yes** |
| License / README / cite | `LICENSE`, `README.md`, `CITATION.cff` | **yes** |
| PoC / pack scripts | `scripts/run_poc.sh`, `scripts/pack_artifact.sh`, `scripts/print_matrix.py` | **yes** / best-effort |
| Paper docs | `paper/READINESS.md`, `SUBMISSION.md`, `ARTIFACT_CHECKLIST.md`, `NOTES.md`, `README.md`, `RQ_PROTOCOL.md`, `ANON.md`, `WITNESSES.md` | **yes** when present |
| Paper source | `paper/one-trace-is-not-enough.tex`, `paper/refs.bib` | **yes** when present |
| Paper PDF | `paper/one-trace-is-not-enough.pdf` | **optional** (bundled only if already built) |
| Figures | `docs/images/*.{png,svg}` | if present |
| Adapter docs | `docs/adapters/` (incl. `WITNESSES.md`, `GATING.md`) | if present |
| Harness trees | `diptych/`, `ops/`, `controls/`, `diptych-probes/`, `tests/` | if present |
| Root helpers | `Makefile`, `pyproject.toml`, `PAPER_OUTLINE.md`, `CHANGELOG.md` | if present |
| Matrix snapshot | `artifact/matrix_snapshot.txt` | generated |

**PDF attachment:** if `paper/one-trace-is-not-enough.pdf` exists (from
`make paper`), it is bundled. Otherwise `ARTIFACT_NOTES.txt` explains how to
download CI workflow artifact **`one-trace-is-not-enough-pdf`** from Actions and
place the PDF under `paper/` (or submit alongside the zip).

| Exclude | Why |
|---------|-----|
| ZeroDay / AOMB product trees | Not vendored; pins only |
| Exploit / attack payloads | Contract forbid |
| Invented model scores / AUROC tables | Non-claim; `tab:placeholder` stays `---` |
| Secrets, tokens, private emails beyond author block | Privacy |

### Suggested zip layout

```
DIPTYCH-<shortsha>/
  README.md
  CITATION.cff
  CHANGELOG.md
  CODE_SHA.txt
  ARTIFACT_NOTES.txt
  coverage/matrix.json
  artifact/matrix_snapshot.txt
  examples/poc/expected_matrix_snippet.json
  adapters/PINS.md
  paper/one-trace-is-not-enough.pdf   # optional; else attach from CI
  paper/READINESS.md
  paper/SUBMISSION.md
  paper/RQ_PROTOCOL.md
  paper/ANON.md
  paper/ARTIFACT_CHECKLIST.md
  paper/one-trace-is-not-enough.tex
  paper/refs.bib
  docs/adapters/…
  docs/images/…
  diptych/ ops/ controls/ diptych-probes/ tests/ scripts/
  LICENSE Makefile …
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

- [ ] Readiness scorecard reviewed: [`READINESS.md`](READINESS.md)
- [ ] Root `LICENSE` (MIT) present and cited (README / this file / pack zip)
- [ ] Cite path present: root `CITATION.cff` (no fake DOI) + README cite snippet
- [ ] PDF builds (`make paper` or CI `paper-pdf`); page count recorded in §2 + `NOTES.md`
- [ ] `./scripts/run_poc.sh` exit 0; matrix matches snippet
- [ ] `make artifact` / `./scripts/pack_artifact.sh` produces zip with CODE_SHA + matrix (CI dry-runs)
- [ ] Zip inventory matches §3 (no ZeroDay/AOMB product trees)
- [ ] Pins in PDF / checklist match `adapters/PINS.md` (frozen as of main `84930b24`)
- [ ] Table `tab:ops` coupling + graded channels match
      `docs/adapters/OPERATOR_TABLE.md` / `ops/*/spec.yaml`
- [ ] Table `tab:placeholder` has **no** numeric model scores
- [ ] RQ1–RQ5 result cells stay N/A (`paper/RQ_PROTOCOL.md`)
- [ ] Discussion states green×8×3 proves coverage+axis power only; not RQ answers
- [ ] Witness appendix / `docs/adapters/WITNESSES.md` present for reviewer audit
- [ ] Threats § lists concrete DIPTYCH limits (pins, inconclusive, controls, coupling)
- [ ] Venue name + deadline + page limit filled in §0
- [ ] Anonymization path dry-run documented (`paper/ANON.md`) if double-blind
- [ ] Abhinav approves merge / camera-ready (HOLD for yes)

---

## 5. Anonymization notes (if double-blind)

**Authoritative switch + dry-run steps:** [`ANON.md`](ANON.md).

Venue remains TBD — do not invent one. CI builds the **named** author PDF only;
do **not** fail CI on anonymization. Apply the switch locally (or in a
pre-upload branch) only when the CFP requires double-blind.

If the venue requires anonymity:

1. Comment named `\author` / uncomment anonymous block (exact steps in `ANON.md`).
2. Redact GitHub URLs that deanonymize (`pandeyaby/DIPTYCH` → “supplementary
   anonymous repo” or CMT upload). Prefer an anonymized mirror or zip-only
   artifact for review. (Header `%%` comments do not appear in the PDF.)
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

See also: [`READINESS.md`](READINESS.md), [`ARTIFACT_CHECKLIST.md`](ARTIFACT_CHECKLIST.md),
[`NOTES.md`](NOTES.md), [`RQ_PROTOCOL.md`](RQ_PROTOCOL.md), [`ANON.md`](ANON.md),
[`WITNESSES.md`](WITNESSES.md), root [`LICENSE`](../LICENSE),
[`docs/adapters/GATING.md`](../docs/adapters/GATING.md),
[`docs/adapters/WITNESSES.md`](../docs/adapters/WITNESSES.md).
