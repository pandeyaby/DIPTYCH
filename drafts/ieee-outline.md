# IEEE draft outline — DIPTYCH

**Authoritative IEEEtran (source of truth):**
[`paper/one-trace-is-not-enough.tex`](../paper/one-trace-is-not-enough.tex)

**Authors:** Abhinav Pandey, Abhishek Pandey (Meta)  
**Companion stubs:** [`ieee-draft.md`](ieee-draft.md) (non-authoritative; trimmed)

Do not invent AUROC / model scores. Do not edit ZeroDay / AOMB product trees.

| § | Topic (living `.tex`) |
|---|------------------------|
| I | Introduction / film metaphor |
| II–IV | Taxonomy, audit, obstruction |
| V–VI | Harness + operators + `gate_axis_mutate` |
| VII | Evaluation **protocol** + green×8×3 coverage (not RQ answers) |
| — | Related work |
| — | **Discussion** — what green×8×3 proves vs does not; RQ N/A |
| — | Threats to validity |
| — | Conclusion |
| App. | Witness / adapter audit trail → `docs/adapters/WITNESSES.md` |

**Offline fact:** green×8×3 = coverage + axis power at pins
ZeroDay@`fb5b39da` / AOMB@`667e475` (`coverage/matrix.json`).

**RQ1–RQ5:** harness scaffolding with **N/A** result cells
(`paper/RQ_PROTOCOL.md`). Checklist: `paper/ARTIFACT_CHECKLIST.md`.
