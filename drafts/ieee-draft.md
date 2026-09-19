# One Trace Is Not Enough: Hyperproperty Grading for Agent-Authored Control Systems

**Authors:** Abhishek Pandey (Meta), Abhinav Pandey (Cisco)  
**Artifact:** https://github.com/pandeyaby/DIPTYCH · schema `0.2`  
**Authoritative IEEEtran:** [`paper/one-trace-is-not-enough.tex`](../paper/one-trace-is-not-enough.tex)  
**Artifact checklist:** [`paper/ARTIFACT_CHECKLIST.md`](../paper/ARTIFACT_CHECKLIST.md)  
**Pins (read-only):** ZeroDay `@fb5b39daf88e37521aaee8526ae9d286cf74f341` · AOMB `@667e47538ae5b9c504187b7a73220d22aa8fb96f`  
**Outline sources:** [`PAPER_OUTLINE.md`](../PAPER_OUTLINE.md) · [`drafts/ieee-outline.md`](ieee-outline.md)

---

## Abstract

Agentic coding benchmarks grade a candidate artifact by executing it and scoring
the resulting trace. We show that this evaluation model is structurally unable to
express requirements that are *hyperproperties*—predicates over sets of traces.
**DIPTYCH** grades calibration as **2-safety**: a coupled pair sharing all
exogenous inputs except one controlled perturbation. It ships schema-`0.2` probe
envelopes, eight operators (open-loop and CRN closed-loop), and
`gate_axis_mutate` power-on-axis. Product adapters (ZeroDay, AOMB) emit
conforming/violating twins at pinned SHAs; DIPTYCH grades and does not vendor
those trees. Live offline evaluation reports a full **green×8×3** coverage
matrix—`diptych_core`, `zeroday`, and `aomb` all green for every operator, with
`axis_power: true`—citing ZeroDay@`fb5b39da` (merged #41+#42) and AOMB@`667e475`
(merged #18). Section VII of the living `.tex` is an evaluation **protocol**;
the model study is in progress and **no model scores are claimed**.

> One film = legal; only the pair = calibrated.

---

## I. Introduction

Agent-authored control stacks are often shipped with a **single-trace demo**: one
seed, one schedule, one cassette replay. A lone film can look legal while hiding
that claimed calibration depends on an unstated coupling. Single-run demos therefore
overclaim.

**Hyperproperty framing.** Clarkson and Schneider’s hyperproperties lift properties
from individual traces to sets of traces. Calibration-as-robustness under a
controlled axis is a classic **2-safety** pattern. DIPTYCH operationalizes that
pattern for agent-authored controllers (see living `.tex` §I–II and
Fig. `fig:diptych` / `docs/images/diptych-vs-single-trace.png`).

**Contributions** (aligned with the `.tex`):

1. Property taxonomy for agentic evaluation + audit of a 22-invariant orchestrator spec.
2. State-confounding obstruction (paired execution required for asymmetry).
3. **DIPTYCH harness** — schema `0.2`, eight operators, CRN coupling, probe trees, `gate_axis_mutate`.
4. **Evaluation protocol** with handwritten controls; ZeroDay/AOMB at pinned SHAs as adapters feeding DIPTYCH (Fig. `fig:stack`).

**Non-claims (stated up front; match GATING).** Localization ≠ exploitability. No
exploit/PoC payloads. No AUROC / model-score fields in graded envelopes. Protocol +
handwritten controls first; model study in progress. Adapter greens cite pinned
merge SHAs only—never invented model scores. `inconclusive` ≠ green.

---

## II. Related Work and Hyperproperty Taxonomy

See living `.tex` §§ taxonomy / related. Summary:

| Layer | Question | DIPTYCH answer |
|---|---|---|
| Trace property | Does one film satisfy a local invariant? | Necessary but insufficient for calibration |
| 2-safety hyperproperty | Do coupled twins respect the axis? | Graded via conforming/violating pairs |
| Power-on-axis | Does mutating *only* the axis flip pass→fail? | `gate_axis_mutate` |
| Incomparability | Are twins even comparable? | Discard / `inconclusive` (≠ green) |

| Discipline | Shared exogenous mass | Graded axes |
|---|---|---|
| **Open-loop** (`open_loop`) | Shared prefix / cassette / freeze mask | FREEZEDRY, RESEED, SCHEMAX, SIGNFLIP, SATEXTEND, HISTSWAP |
| **CRN closed-loop** (`crn_closed_loop`) | Shared CRN stream + closed-loop residual | TRAJSWAP, VARSCALE |

Verdict vocabulary: `{pass, fail, inconclusive}`. Coupling enums match
`ops/*/spec.yaml` and `docs/adapters/OPERATOR_TABLE.md`.

---

## III. DIPTYCH Model: Harness and `gate_axis_mutate`

Hard keys (contract): `diptych_schema`, `source`, `operator`, `coupling`,
`probe_id`, `control_role`, `traces` (length ≥ 2), `expected_verdict`.

A coverage cell is **green** only when (1) conforming → `pass`, (2) violating →
`fail`, and (3) `gate_axis_mutate` reports power. Cosmetic edits (verdict-only,
SARIF level rename, AUROC inject) fail the axis gate.

Layout: `diptych/`, `ops/`, `controls/`, `diptych-probes/`, `coverage/matrix.json`,
`adapters/PINS.md`, `scripts/run_poc.sh`.

---

## IV. Eight-Operator Table

| Op | Coupling | Primary graded axis | Conforming (→ pass) | Violating (→ fail) |
|---|---|---|---|---|
| **FREEZEDRY** | `open_loop` | freeze mask / fingerprints | identical under freeze | diverge when unfrozen |
| **RESEED** | `open_loop` | `meta.seed`, stability ≤ ε | within ε | exceeds ε |
| **SCHEMAX** | `open_loop` | required schema key set | sets equal | key rename/drop |
| **SIGNFLIP** | `open_loop` | polarity channel | invariant under flip | polarity broken |
| **SATEXTEND** | `open_loop` | saturation bounds | property holds | sat into violation |
| **HISTSWAP** | `open_loop` | history splice | property holds | corrupt history |
| **TRAJSWAP** | **`crn_closed_loop`** | trajectory + residual | residual in bound | swap breaks CRN invariant |
| **VARSCALE** | **`crn_closed_loop`** | mean-matched variance scale | within bound | breaks bound (**not** AUROC) |

Canonical: [`docs/adapters/OPERATOR_TABLE.md`](../docs/adapters/OPERATOR_TABLE.md).

---

## V. Methods

```bash
./scripts/run_poc.sh
# or: make poc
```

Exit `0` ⇒ full-8 `diptych_core` green (twin contrast + axis power). No GPU.
No network required for the core PoC.

| Adapter | Full SHA | Short |
|---|---|---|
| **ZeroDay** | `fb5b39daf88e37521aaee8526ae9d286cf74f341` | `fb5b39da` |
| **AOMB** | `667e47538ae5b9c504187b7a73220d22aa8fb96f` | `667e475` |

Source: [`adapters/PINS.md`](../adapters/PINS.md). This draft does **not** modify
ZeroDay or AOMB product trees.

**Refuse to report:** AUROC / invented model grades; exploit payloads; green
adapter cells without pin evidence.

---

## VI. Evaluation (protocol + live offline matrix)

Aligned with `.tex` §VII. **No model scores.**

Matrix path: [`coverage/matrix.json`](../coverage/matrix.json). Notes cite
ZeroDay@`fb5b39daf88e37521aaee8526ae9d286cf74f341` (merged #41+#42) and
AOMB@`667e47538ae5b9c504187b7a73220d22aa8fb96f` (merged #18).

| Operator | diptych_core | zeroday | aomb | axis_power |
|---|---|---|---|---|
| FREEZEDRY | **green** | **green** | **green** | true |
| RESEED | **green** | **green** | **green** | true |
| SCHEMAX | **green** | **green** | **green** | true |
| SIGNFLIP | **green** | **green** | **green** | true |
| SATEXTEND | **green** | **green** | **green** | true |
| HISTSWAP | **green** | **green** | **green** | true |
| TRAJSWAP | **green** | **green** | **green** | true |
| VARSCALE | **green** | **green** | **green** | true |

**Summary:** live **green×8×3**. No AUROC column. Model-study power/separation
table in the `.tex` remains `---` until the study completes.

Negative controls: cosmetic mutators fail `gate_axis_mutate` (unit tests).

```bash
./scripts/run_poc.sh   # expect exit 0; refreshes coverage/matrix.json
```

---

## VII. Threats to Validity

**Internal.** Handwritten controls establish protocol power, not fleet-wide
generalization. Graders are deterministic functions of envelope fields.

**Construct.** “Green” = twin contrast + axis power under schema `0.2`, not
exploitability or model quality.

**External.** Adapter greens pinned to ZeroDay@`fb5b39da` / AOMB@`667e475`;
product trees not vendored. Pin move without QC drops columns out of green.

**Conclusion.** No AUROC improvements, ranking lifts, or exploit findings.
Claims limited to harness behavior, pin/merge facts, and live matrix cells.

---

## VIII. Limitations, Ethics, and Non-Claims

- Localization ≠ exploitability; no attack PoCs.
- No AUROC / model scores; contract rejects those fields.
- `inconclusive` ≠ green.
- Pins only; ZeroDay/AOMB product trees not edited here.
- Ethics: desk-safe fingerprints/fixtures; omit exploit/payload fields.

---

## IX. Conclusion

DIPTYCH treats calibration as a **2-safety** grading problem over coupled traces.
Live offline evaluation paints **green×8×3** at the cited pins, with axis power
on every operator. One film can be legal; only the pair is calibrated. The model
study remains in progress—no fabricated scores.

---

## Appendix A — Artifact checklist

See [`paper/ARTIFACT_CHECKLIST.md`](../paper/ARTIFACT_CHECKLIST.md) for code,
controls, logs, non-claims, and how to reproduce the matrix + PoC.

| Item | Location |
|---|---|
| Living IEEEtran | `paper/one-trace-is-not-enough.tex` |
| Checklist | `paper/ARTIFACT_CHECKLIST.md` |
| Live matrix | `coverage/matrix.json` |
| Pins | `adapters/PINS.md` |
| PoC | `./scripts/run_poc.sh` |
| Contract / gating | `docs/adapters/CONTRACT.md`, `GATING.md` |
| Figures | `docs/images/diptych-vs-single-trace.png`, `stack.png` |

## Appendix B — Citation policy for metrics

Cite only: repository facts, CI exit codes, and cells written to
`coverage/matrix.json`. Do not invent AUROC, accuracy, or model-score numbers.
