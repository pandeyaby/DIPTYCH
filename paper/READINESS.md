# Submission readiness scorecard — DIPTYCH

One-page honest gate for IEEE upload. **Harness-only.** No invented AUROC /
model scores. No venue name or DOI invented here.

**As of:** main after #11 @ `4576506a6611ab02457190b7132fde412c21a8c6`
(scorecard + LICENSE/cross-link sync; 8pp PDF and `CITATION.cff` from #10).
Adapter pins remain frozen as of main `84930b24`
(ZeroDay@`fb5b39da` · AOMB@`667e475`).

**HOLD for Abhinav merge yes** before treating any upload as submission-final.

Milestone log: root [`CHANGELOG.md`](../CHANGELOG.md) (#2–#11). Companion docs:
[`SUBMISSION.md`](SUBMISSION.md) · [`ARTIFACT_CHECKLIST.md`](ARTIFACT_CHECKLIST.md) ·
[`RQ_PROTOCOL.md`](RQ_PROTOCOL.md) · [`ANON.md`](ANON.md) ·
[`NOTES.md`](NOTES.md) · root [`LICENSE`](../LICENSE) ·
[`CITATION.cff`](../CITATION.cff)

---

## Stop condition

Engineering polish is at **diminishing returns**. The next material unlock is
Abhinav filling venue / author fields (`SUBMISSION.md` §0) or a real model-study
data drop (RQ / `tab:placeholder` still **N/A**). Do not invent venue, DOI, or
scores to force progress. See [`NOTES.md`](NOTES.md) and [`CHANGELOG.md`](../CHANGELOG.md).

---

## Green (automated / already present)

| Item | Status | Where |
|------|--------|-------|
| **CI** (pytest + PoC + artifact dry-run + `paper-pdf`) | green on main | `.github/workflows/ci.yml` |
| **Stranger PoC** | exit 0; `MATRIX CHECK OK` | `./scripts/run_poc.sh` / `make poc` |
| **Coverage matrix** green×8×3 + `axis_power=true` | live | `coverage/matrix.json` ↔ `examples/poc/expected_matrix_snippet.json` |
| **PDF build** | **8 pages** (IEEEtran conference) | `make paper` or CI artifact `one-trace-is-not-enough-pdf` |
| **Cite path** | present; **no DOI** | root [`CITATION.cff`](../CITATION.cff) + README BibTeX snippet |
| **License** | MIT at root; cited | [`LICENSE`](../LICENSE) · README · pack zip |
| **ANON path** | dry-run documented; CI stays named | [`ANON.md`](ANON.md) |
| **Adapter pins** | frozen; product trees not vendored | [`adapters/PINS.md`](../adapters/PINS.md) |
| **Artifact pack** | zip with CODE_SHA + matrix + LICENSE + cite | `make artifact` / `scripts/pack_artifact.sh` |
| **Non-claims surviving** | no AUROC / exploit payloads / product edits | GATING + paper §threats + this scorecard |

green×8×3 proves **harness coverage + `gate_axis_mutate` axis power** at the
frozen pins only — not accuracy, vulnerability finding, or RQ answers.

---

## Still human TODOs (do not invent)

| Item | Status | Notes |
|------|--------|-------|
| **Venue name** | TODO | Fill `SUBMISSION.md` §0 from a real CFP — never invent |
| **CFP deadline** | TODO | Record YYYY-MM-DD (+ timezone as CFP states) |
| **Page limit vs 8pp** | TODO | Compare measured 8pp to CFP; trim only after limit known |
| **Corresponding affiliation line** | TODO | Abhinav emails present; affiliation string for camera-ready TBD |
| **Author / CMT contact confirm** | TODO | Confirm `pandey.aby@gmail.com` in venue form before upload |
| **Optional Zenodo (or similar) DOI** | optional / skip | Do **not** invent a DOI; mint only if intentionally published |
| **Bibliography DOI/page polish** | TODO | Verify `refs.bib` for camera-ready |
| **Double-blind apply** | if CFP requires | Follow `ANON.md`; default CI stays named |
| **Abhinav merge / camera-ready yes** | **HOLD** | Required before upload |

---

## Honest N/A (model study)

| Claim | Status |
|-------|--------|
| RQ1–RQ5 measured results | **N/A** — protocol scaffolding only ([`RQ_PROTOCOL.md`](RQ_PROTOCOL.md)) |
| Table `tab:placeholder` model power / separation | **N/A** — cells stay `---` |
| AUROC / F1 / `model_grade` / accuracy | **N/A** — contract rejects; not claimed |
| Measured probe-tree α (tick counts) | **N/A** — Definition `def:cost` is protocol only |
| Vuln-finding / exploitability from green×8×3 | **N/A** — localization ≠ exploitability |

Fill model-study cells only from real held-out artifacts and scenarios. Do not
back-fill from the coverage matrix.

---

## Quick stranger path

```bash
./scripts/run_poc.sh          # expect MATRIX CHECK OK
make artifact                 # LICENSE + CITATION.cff + paper/READINESS.md in zip
make paper                    # or download CI one-trace-is-not-enough-pdf
```

See also: root [`README.md`](../README.md) · [`paper/README.md`](README.md) ·
[`CHANGELOG.md`](../CHANGELOG.md).
