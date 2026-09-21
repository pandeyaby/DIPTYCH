# Changelog — DIPTYCH (IEEE harness polish)

Harness-only milestone log. **No ZeroDay / AOMB product edits.** No invented
AUROC, model scores, venue names, or DOIs.

**Current status:** see [`paper/READINESS.md`](paper/READINESS.md) (frozen as of
main `4576506a` after #11). Human TODOs (venue, emails/affiliation, optional DOI)
and model-study cells remain open / N/A.

**HOLD for Abhinav merge yes** before treating any upload as submission-final.

---

## Unreleased (CODE-FIRST harness)

| PR | Milestone |
|----|-----------|
| (draft) | RQ5 protocol harness — `diptych.inconclusive` / `diptych-inconclusive`: inconclusive rate on cassette fixtures (`n_inconclusive/n_probes`, reason-needle strata; structural counts only, not AUROC / not empirical RQ5); smoke schema **1.6** + inconclusive step |
| (draft) | RQ2 protocol harness — `diptych.ablation` / `diptych-ablation`: operator leave-one-out on cassette controls; `marginal_necessary` / `redundancy_with_peers` (structural flags only, not AUROC / not empirical RQ2); smoke schema **1.5** + ablation step |
| (draft) | RQ1 protocol harness — `diptych.separation` / `diptych-separation`: single-trace baseline vs hyperproperty on cassette controls; `control_separation_index` (structural control-bank fraction only, not AUROC); smoke schema **1.4** + separation step |
| #23 (draft) | Installable console scripts (`diptych`, `diptych-poc` / `grade` / `matrix` / `schema` / `full8`) + `__version__` 0.2.0 aligned with pyproject; entry-point tests |
| #22 | Unified stranger smoke (`python -m diptych` / `make smoke`) — schema + cassette grade + matrix + poc + thin reject |
| #21 | Executable CONTRACT v0.2 JSON Schema (`docs/adapters/diptych_schema_0.2.json`) + `python -m diptych.schema --check` / `--dump-schema` |

---

## Merged IEEE polish (#2–#11)

| PR | Merge SHA | Milestone |
|----|-----------|-----------|
| [#2](https://github.com/pandeyaby/DIPTYCH/pull/2) | `03e5dd45` | IEEEtran paper + diagrams (world-ready artifact skeleton) |
| [#3](https://github.com/pandeyaby/DIPTYCH/pull/3) | `fd6e215a` | Align paper + artifact path to live green×8×3 matrix |
| [#4](https://github.com/pandeyaby/DIPTYCH/pull/4) | `a43eda91` | PDF CI + BibTeX / case-study polish |
| [#5](https://github.com/pandeyaby/DIPTYCH/pull/5) | `40358a4e` | Stranger PoC + figure / repro polish |
| [#6](https://github.com/pandeyaby/DIPTYCH/pull/6) | `d862ed8c` | Submission package + threats + operator-table fidelity |
| [#7](https://github.com/pandeyaby/DIPTYCH/pull/7) | `e9c9d108` | Methods depth + RQ scaffolding + artifact pack |
| [#8](https://github.com/pandeyaby/DIPTYCH/pull/8) | `84930b24` | Discussion + witness appendix + figure/pack polish; **adapter pins frozen** |
| [#9](https://github.com/pandeyaby/DIPTYCH/pull/9) | `19075a3a` | Venue TODOs, page budget, ANON dry-run, pin freeze line |
| [#10](https://github.com/pandeyaby/DIPTYCH/pull/10) | `98eb0efa` | Abstract ≤250w, `CITATION.cff`, living draft **8pp** |
| [#11](https://github.com/pandeyaby/DIPTYCH/pull/11) | `4576506a` | `paper/READINESS.md` scorecard + LICENSE / cross-link sync |

Earlier: [#1](https://github.com/pandeyaby/DIPTYCH/pull/1) (`266ed6cd`) — IEEE draft + CI (pytest/PoC workflow).

---

## Stop condition (engineering)

Harness polish is at **diminishing returns**. Further material unlocks are
human- or data-gated:

1. Abhinav fills venue / author fields in `paper/SUBMISSION.md` §0 (never invent)
2. A real model-study data drop (RQ1–RQ5 / `tab:placeholder` stay **N/A** until then)

Cosmetic doc churn alone is not a reason for another polish PR.
