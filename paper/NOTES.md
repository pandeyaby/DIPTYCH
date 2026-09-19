# Paper notes — open items

Living source: [`one-trace-is-not-enough.tex`](one-trace-is-not-enough.tex)  
Artifact checklist: [`ARTIFACT_CHECKLIST.md`](ARTIFACT_CHECKLIST.md)  
Submission package: [`SUBMISSION.md`](SUBMISSION.md)

## Status

- Methods, audit, harness, operators, metrics **definitions**: complete.
- Section VII = evaluation **protocol** + offline harness facts only.
- Section IX threats: concrete DIPTYCH limits (pin drift, inconclusive, control bias, open-loop vs CRN).
- Model study: **in progress** — no scores claimed; Table `tab:placeholder` stays `---`.
- Camera-ready packaging notes live in `SUBMISSION.md` (venue TBD; HOLD for Abhinav merge yes).
- Stranger PoC: `./scripts/run_poc.sh` prints green×8×3 and checks
  `examples/poc/expected_matrix_snippet.json` (CI + local).

## Citation / pin verification (do not invent)

| Claim | Source |
|-------|--------|
| Adapter pins | `adapters/PINS.md`, root `README.md` |
| Coverage cells | `coverage/matrix.json` |
| Coupling enums | `ops/*/spec.yaml`, `docs/adapters/OPERATOR_TABLE.md` |
| Green rule / non-claims | `docs/adapters/GATING.md`, `docs/adapters/CONTRACT.md` |

Current pins: ZeroDay@`fb5b39da` · AOMB@`667e475`.

## Open (post-protocol)

- [ ] Fill Table `tab:placeholder` only after model-study completion (power / sep.).
- [ ] Measure RQ1–RQ5 on held-out scenarios; report uncertainty over artifact + scenario sampling only.
- [ ] Fill venue name + page limit in `SUBMISSION.md`.
- [ ] Verify bibliography DOIs / page numbers for camera-ready.

## Authorship

Paper authors: **Abhinav Pandey**, **Abhishek Pandey (Meta)**. Do not invent
`Co-authored-by` trailers unless git history / this NOTES file records a real
contribution path for Abhishek on a given commit.

## Hard non-claims

No AUROC / model grades in envelopes · no exploit payloads · `inconclusive` ≠ green ·
adapters are pins feeding DIPTYCH, not in-repo product code ·
green×8×3 = coverage / axis power only (not vuln-finding or accuracy).
