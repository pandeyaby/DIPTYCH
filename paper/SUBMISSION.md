# Submission package — DIPTYCH

Packaging notes for [`one-trace-is-not-enough.tex`](one-trace-is-not-enough.tex).
Status and pre-upload checklist: [`READINESS.md`](READINESS.md).

**Corresponding author:** Abhinav Pandey (Independent Researcher) · `pandey.aby@gmail.com`
**Co-author:** Abhishek Pandey (Meta) · `pandeyabhi1987@gmail.com`

## 1. Venue

| Field | Value |
|-------|-------|
| **Venue** | AGENT'27 — Second International Workshop on Agentic Engineering, co-located with ICSE 2027 (Dublin) · https://conf.researchr.org/home/icse-2027/agent-2027 |
| **Workshop date** | Mon 26 Apr 2027 |
| **Deadlines** | Paper 2026-11-27 · notification 2026-12-11 · camera-ready 2027-01-29 |
| **Submission** | HotCRP https://icse2027-agent.hotcrp.com/ |
| **Page limit** | Full paper ≤ 8 pages; short paper ≤ 5 pages; references excluded |
| **Template** | IEEE conference proceedings (`IEEEtran`, conference) |
| **Review** | Single-anonymous: named authors, public repository is fine |
| **Artifact option** | Not stated in the CFP; the paper links the public repository |
| **Topic fit** | Verification and testing of agentic systems; evaluation methodology |

**Fallback if rejected (sequential, never concurrent).** AGENT'27 notifies on
2026-12-11, before the ICST 2027 New Ideas and Emerging Results deadline
(2027-01-13). Revise with the reviews and resubmit there. Do not submit this
paper anywhere else while it is under review.

## 2. Page budget

| Build | Result |
|-------|--------|
| Current draft | Body ends on page 8; references on pages 8–9 (fits "8 pages excluding references") |
| Largest floats | Operator table (`table*`), audit table (`table*`), Tables IV (adequacy) and V (pilot), Figure 1 |

If a later edit overflows, trim in this order: related-work density, survivor
triage prose in §VIII-C, Figure 1 width. Build with `make paper` (needs
`latexmk` + TeX Live with `IEEEtran`) or download the CI artifact
`one-trace-is-not-enough-pdf`.

## 3. Artifact zip

```bash
make artifact          # or ./scripts/pack_artifact.sh → dist/DIPTYCH-<shortsha>.zip
```

The zip holds `CODE_SHA.txt`, `ARTIFACT_NOTES.txt`, `LICENSE`, `README.md`,
`CITATION.cff`, `coverage/matrix.json`, the PoC snippet, `adapters/PINS.md`,
the paper source and docs, figures, adapter docs, and the harness trees
(`diptych/` including `diptych/pilot/` with the generated controllers,
`ops/`, `controls/`, `diptych-probes/`, `tests/`). The PDF is bundled when
`paper/one-trace-is-not-enough.pdf` exists; otherwise attach the CI artifact.
ZeroDay / AOMB product trees are not vendored (pins only).

Reproduce from the zip:

```bash
./scripts/run_poc.sh                  # coverage matrix; expect MATRIX CHECK OK
python -m diptych.adequacy            # Table IV
python -m diptych.pilot.study         # Table V (no model calls)
PYTHONPATH=. python -m pytest -q      # needs pytest
```

## 4. Non-claims

- No accuracy, AUROC, F1, or model ranking; the envelope contract rejects score fields.
- The pilot shows paired grading changes verdicts on 20 single-shot controllers; it is not a model comparison.
- A green coverage cell means axis power at the pinned adapters, not a vulnerability finding.
- `inconclusive` is never counted as green.

See also: [`ARTIFACT_CHECKLIST.md`](ARTIFACT_CHECKLIST.md) · [`RQ_PROTOCOL.md`](RQ_PROTOCOL.md) ·
[`ANON.md`](ANON.md) (only if a future venue is double-blind) · [`WITNESSES.md`](WITNESSES.md).
