# Paper notes — open items

Living source: [`one-trace-is-not-enough.tex`](one-trace-is-not-enough.tex)  
Artifact checklist: [`ARTIFACT_CHECKLIST.md`](ARTIFACT_CHECKLIST.md)  
Submission package: [`SUBMISSION.md`](SUBMISSION.md)  
RQ scaffolding: [`RQ_PROTOCOL.md`](RQ_PROTOCOL.md)  
Anonymization switch: [`ANON.md`](ANON.md)

## Status

- Methods, audit, harness, operators, metrics **definitions**: complete.
- §harness expanded: open-loop + CRN defs, comparability horizon, inconclusive
  verdicts (match `diptych.VERDICTS` / GATING), probe-tree cost as **protocol**
  definition (`def:cost`) — not measured $.
- Section VII = evaluation **protocol** + offline harness facts only; RQ1–RQ5
  have harness-answer scaffolding with N/A result cells.
- **Discussion** states what green×8×3 proves (coverage + axis power) vs does
  not (vuln finding, model accuracy); cross-links RQ_PROTOCOL N/A cells.
- Threats §: pin drift, inconclusive, control bias, open-loop vs CRN (trimmed).
- Witness appendix + `docs/adapters/WITNESSES.md` / `paper/WITNESSES.md` for
  reviewer audit without product repos.
- Model study: **in progress** — no scores claimed; Table `tab:placeholder`
  stays reserved blank (`---`).
- Camera-ready packaging notes live in `SUBMISSION.md` (venue TODOs; HOLD for
  Abhinav merge yes).
- Artifact pack: `make artifact` / `./scripts/pack_artifact.sh` → `dist/DIPTYCH-<sha>.zip`
  (CI dry-runs after PoC); inventory listed in `SUBMISSION.md` §3.
- ANON dry-run verified (comment named `\author`, uncomment anonymous block;
  PDF shows Anonymous Author(s); no author-email leaks). CI stays named-only.
- Stranger PoC: `./scripts/run_poc.sh` prints green×8×3 and checks
  `examples/poc/expected_matrix_snippet.json` (CI + local).
- Markdown drafts under `drafts/` are **stubs**; `.tex` is source of truth.

## PDF page count (IEEEtran)

| When | Command | Pages | Note |
|------|---------|------:|------|
| This polish draft | `make paper` / `latexmk` | **9** | ≈8pp body+appendix; refs start p8, ~6-line spill to p9 |
| Post-#8 baseline | (prior) | ~10 | Trimmed related-work / duplicate pins / compact `tab:placeholder` |

Venue page limit is still **TODO** in `SUBMISSION.md` §0. Further trim only
after the CFP limit is known; do not invent AUROC/model scores to fill space
or to force a 6pp cut that would gut honest protocol scaffolding.

## Citation / pin verification (do not invent)

| Claim | Source |
|-------|--------|
| Adapter pins | `adapters/PINS.md`, root `README.md` |
| Coverage cells | `coverage/matrix.json` |
| Coupling enums | `ops/*/spec.yaml`, `docs/adapters/OPERATOR_TABLE.md` |
| Green rule / non-claims | `docs/adapters/GATING.md`, `docs/adapters/CONTRACT.md` |
| RQ result cells | `paper/RQ_PROTOCOL.md` (all N/A until model study) |
| Witness recipes | `docs/adapters/WITNESSES.md`, `paper/WITNESSES.md` |

**Pins frozen as of main `84930b24`:** ZeroDay@`fb5b39da` · AOMB@`667e475`
(unchanged; do not bump unless the matrix requires).

## Open (post-protocol)

- [x] Record PDF page count after latexmk (`NOTES` + `SUBMISSION` §2)
- [x] ANON dry-run documented (`ANON.md` §2)
- [x] Artifact zip inventory matches `make artifact` (`SUBMISSION` §3)
- [x] Pin freeze line (main `84930b24`)
- [ ] Discussion / threats still honest on green×8×3 vs RQ N/A after any edit
- [ ] Witness appendix paths resolve (`docs/adapters/WITNESSES.md`)
- [ ] Fill Table `tab:placeholder` only after model-study completion (power / sep.).
- [ ] Measure RQ1–RQ5 on held-out scenarios; report uncertainty over artifact + scenario sampling only.
- [ ] Fill venue name + deadline + page limit in `SUBMISSION.md` §0.
- [ ] Verify bibliography DOIs / page numbers for camera-ready.
- [ ] If double-blind: apply `ANON.md` switch before upload (not on default CI).
- [ ] If venue is strict 6pp inclusive: further trim per `SUBMISSION.md` order.

## Authorship

Paper authors: **Abhinav Pandey**, **Abhishek Pandey (Meta)**. Do not invent
`Co-authored-by` trailers unless git history / this NOTES file records a real
contribution path for Abhishek on a given commit.

## Hard non-claims

No AUROC / model grades in envelopes · no exploit payloads · `inconclusive` ≠ green ·
adapters are pins feeding DIPTYCH, not in-repo product code ·
green×8×3 = coverage / axis power only (not vuln-finding or accuracy) ·
probe-tree α is a protocol definition until tick counts are measured.
