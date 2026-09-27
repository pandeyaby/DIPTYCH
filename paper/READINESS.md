# Submission readiness — DIPTYCH

**Target:** AGENT'27 (Second International Workshop on Agentic Engineering,
ICSE 2027). Paper deadline **2026-11-27** · full paper ≤ 8 pages excluding
references · IEEE template · single-anonymous. Details and fallback plan:
[`SUBMISSION.md`](SUBMISSION.md) §0.

## Done

| Item | Where |
|------|-------|
| Paper compiles (IEEEtran conference); body 8 pages + references | `make paper` or CI artifact `one-trace-is-not-enough-pdf` |
| Evaluation §VIII: controls, coverage, mutation-sweep adequacy (Table IV) | `python -m diptych.adequacy` |
| Pilot on 20 model-written controllers (Table V) | `python -m diptych.pilot.study`; [`diptych/pilot/`](../diptych/pilot/) |
| Grader defects found by the sweep fixed, with regression tests | `tests/test_adequacy.py` |
| Author block: Abhinav Pandey (Independent Researcher), Abhishek Pandey (Meta) | `.tex` author block |
| Venue fields filled | [`SUBMISSION.md`](SUBMISSION.md) §0 |
| CI green (pytest, PoC, artifact pack, paper PDF) | `.github/workflows/ci.yml` |
| License (MIT) and citation metadata (no DOI) | [`LICENSE`](../LICENSE), [`CITATION.cff`](../CITATION.cff) |

## Before upload (authors)

- [ ] Both authors read the CI-built PDF end to end, especially §VIII and Threats
- [ ] Abhishek confirms the Meta affiliation line is approved for publication
- [ ] Confirm `pandey.aby@gmail.com` as corresponding contact in HotCRP
- [ ] Verify `refs.bib` entries (venues, pages) for camera-ready
- [ ] Do not submit elsewhere while under review at AGENT'27

## Not claimed

| Claim | Status |
|-------|--------|
| Model comparison / ranking | Not claimed — 5 single-shot samples per model |
| Predictive validity on held-out scenarios (RQ3) | Not measured |
| Accuracy / AUROC / F1 | Not applicable; the envelope contract rejects such fields |
| Vulnerability findings from green coverage cells | Not claimed — green = axis power only |

See also: [`SUBMISSION.md`](SUBMISSION.md) · [`RQ_PROTOCOL.md`](RQ_PROTOCOL.md) ·
[`ARTIFACT_CHECKLIST.md`](ARTIFACT_CHECKLIST.md) · root [`CHANGELOG.md`](../CHANGELOG.md).
