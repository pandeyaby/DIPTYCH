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
- Section IX threats: concrete DIPTYCH limits (pin drift, inconclusive, control bias, open-loop vs CRN).
- Model study: **in progress** — no scores claimed; Table `tab:placeholder` stays `---`.
- Camera-ready packaging notes live in `SUBMISSION.md` (venue TBD; HOLD for Abhinav merge yes).
- Artifact pack: `make artifact` / `./scripts/pack_artifact.sh` → `dist/DIPTYCH-<sha>.zip`.
- Stranger PoC: `./scripts/run_poc.sh` prints green×8×3 and checks
  `examples/poc/expected_matrix_snippet.json` (CI + local).

## Citation / pin verification (do not invent)

| Claim | Source |
|-------|--------|
| Adapter pins | `adapters/PINS.md`, root `README.md` |
| Coverage cells | `coverage/matrix.json` |
| Coupling enums | `ops/*/spec.yaml`, `docs/adapters/OPERATOR_TABLE.md` |
| Green rule / non-claims | `docs/adapters/GATING.md`, `docs/adapters/CONTRACT.md` |
| RQ result cells | `paper/RQ_PROTOCOL.md` (all N/A until model study) |

Current pins: ZeroDay@`fb5b39da` · AOMB@`667e475`.

## Open (post-protocol)

- [ ] Fill Table `tab:placeholder` only after model-study completion (power / sep.).
- [ ] Measure RQ1–RQ5 on held-out scenarios; report uncertainty over artifact + scenario sampling only.
- [ ] Fill venue name + page limit in `SUBMISSION.md`.
- [ ] Verify bibliography DOIs / page numbers for camera-ready.
- [ ] If double-blind: apply `ANON.md` switch before upload.

## Authorship

Paper authors: **Abhinav Pandey**, **Abhishek Pandey (Meta)**. Do not invent
`Co-authored-by` trailers unless git history / this NOTES file records a real
contribution path for Abhishek on a given commit.

## Hard non-claims

No AUROC / model grades in envelopes · no exploit payloads · `inconclusive` ≠ green ·
adapters are pins feeding DIPTYCH, not in-repo product code ·
green×8×3 = coverage / axis power only (not vuln-finding or accuracy) ·
probe-tree α is a protocol definition until tick counts are measured.
