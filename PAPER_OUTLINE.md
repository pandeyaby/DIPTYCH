# PAPER_OUTLINE — DIPTYCH

**Working title:** *One Trace Is Not Enough: Hyperproperty Grading for Agent-Authored Control Systems*

**Venue target:** IEEE (artifact-friendly).

**Living IEEEtran source (authoritative):** [`paper/one-trace-is-not-enough.tex`](paper/one-trace-is-not-enough.tex)  
**Authors:** Abhinav Pandey, Abhishek Pandey (Meta)  
**Artifact checklist:** [`paper/ARTIFACT_CHECKLIST.md`](paper/ARTIFACT_CHECKLIST.md)  
**Markdown draft:** [`drafts/ieee-draft.md`](drafts/ieee-draft.md)  
**IEEE skeleton:** [`drafts/ieee-outline.md`](drafts/ieee-outline.md)

## Thesis

Calibration / safety claims for agent-authored controllers are **2-safety hyperproperties**:
not one run, but a **coupled pair** that shares all exogenous inputs except one controlled
perturbation. DIPTYCH forks a shared prefix (open-loop shape / CRN closed-loop point),
discards incomparable pairs, and probes suffixes only. **One film = legal; only the pair = calibrated.**

## Contributions

1. **DIPTYCH harness** — schema `0.2` envelopes, eight operators, `gate_axis_mutate` power-on-axis.
2. **Operator inventory** — FREEZEDRY, RESEED, SCHEMAX, SIGNFLIP, SATEXTEND, HISTSWAP, TRAJSWAP, VARSCALE.
3. **Adapter contract** — product emitters (ZeroDay, AOMB) produce conforming/violating twins; DIPTYCH grades.
4. **Coverage matrix** — Operator × {diptych_core, zeroday, aomb}; green iff twin contrast + axis power.

## Non-claims (GATING-aligned)

- Localization ≠ exploitability. No PoC/exploit payloads.
- No AUROC / model-score fields in graded envelopes; no fabricated LLM scores.
- Protocol + handwritten controls first; model study in progress (blank results table).
- `inconclusive` ≠ green.
- Adapters do not fork product trees into this repo; they pin SHAs.

## Outline → IEEEtran map (`paper/one-trace-is-not-enough.tex`)

| Topic | TeX |
|-------|-----|
| Introduction | §I |
| Motivating example | §II `sec:motivating` |
| Property taxonomy | §III `sec:taxonomy` |
| Spec audit + confound | §IV `sec:audit` |
| Harness + `gate_axis_mutate` | §V `sec:harness` |
| Eight operators | §VI `sec:operators` |
| Metrics (defs only) | `sec:metrics` |
| **Evaluation protocol** + pins + Table `tab:coverage` | **§VII `sec:protocol`** |
| Related work | `sec:related` |
| Discussion (green×8×3 vs RQ N/A) | `sec:discussion` |
| Threats | `sec:threats` |
| Conclusion | final § |
| Witness appendix | `sec:witnesses` → `docs/adapters/WITNESSES.md` |
| Figs | `fig:diptych`, `fig:stack` → `docs/images/` |

## Artifact

```bash
./scripts/run_poc.sh   # exit 0
```

Pins: **ZeroDay@fb5b39da** (`fb5b39daf88e37521aaee8526ae9d286cf74f341`) · **AOMB@667e475** (`667e47538ae5b9c504187b7a73220d22aa8fb96f`)  
Live matrix: [`coverage/matrix.json`](coverage/matrix.json) (green×8×3: `diptych_core` / `zeroday` / `aomb`; ZeroDay@fb5b39da = merged #41+#42; AOMB@667e475 = merged #18).  
Checklist: [`paper/ARTIFACT_CHECKLIST.md`](paper/ARTIFACT_CHECKLIST.md).
