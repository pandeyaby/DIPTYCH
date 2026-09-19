# One Trace Is Not Enough: Hyperproperty Grading for Agent-Authored Control Systems

**Authors:** Abhinav Pandey<sup>1</sup>, Abhishek Pandey<sup>1</sup>  
**Affiliation:** <sup>1</sup>Independent / affiliation placeholder  
**Artifact:** https://github.com/pandeyaby/DIPTYCH · schema `0.2` · IEEE draft  
**Pins (read-only):** ZeroDay `@fb5b39daf88e37521aaee8526ae9d286cf74f341` · AOMB `@667e47538ae5b9c504187b7a73220d22aa8fb96f`  
**Outline sources:** [`PAPER_OUTLINE.md`](../PAPER_OUTLINE.md) · [`drafts/ieee-outline.md`](ieee-outline.md)

---

## Abstract

Calibration and safety claims for agent-authored controllers are routinely demonstrated with a single execution. We argue that such claims are **2-safety hyperproperties**: the bad thing is not one run, but a **coupled pair** that shares all exogenous inputs except one controlled perturbation. **DIPTYCH** is an IEEE-oriented artifact harness that forks a shared prefix (open-loop shape or common-random-numbers closed-loop point), discards incomparable pairs, and grades suffixes only. It ships schema-`0.2` probe envelopes, eight operators spanning open-loop and CRN closed-loop regimes, and a `gate_axis_mutate` power-on-axis check that rejects cosmetic verdict flips. Product adapters (ZeroDay, AOMB) emit conforming/violating twins at pinned SHAs; DIPTYCH grades and does not vendor those trees. Live offline evaluation on this repository reports a full **green×8×3** coverage matrix—`diptych_core`, `zeroday`, and `aomb` all green for every operator, with `axis_power: true`—citing ZeroDay@`fb5b39da` (merged #41+#42) and AOMB@`667e475` (merged #18) plus prior adapter green QCs. We invent no AUROC or model scores—only repo, merge, and CI facts.

> One film = legal; only the pair = calibrated.

---

## I. Introduction

Agent-authored control stacks—LLM-assisted planners, security triage agents, ranking and closed-loop simulators—are often shipped with a **single-trace demo**: one seed, one schedule, one cassette replay. A lone film can look legal while hiding that the claimed calibration depends on an unstated coupling (shared clock, shared noise, shared schema, shared polarity convention). Single-run demos therefore overclaim.

**Hyperproperty framing.** Clarkson and Schneider’s hyperproperties lift properties from individual traces to sets of traces. Calibration-as-robustness under a controlled axis is a classic **2-safety** pattern: for every comparable pair related by a declared perturbation, a relational verdict must hold. DIPTYCH operationalizes that pattern for agent-authored controllers.

**Film metaphor.** One reel may be admissible under local invariants. Calibration is the **diptych**: two frames that share a prefix and differ on exactly one graded axis. Incomparable pairs are discarded, not papered over with a score.

**Contributions.**

1. **DIPTYCH harness** — schema `0.2` envelopes, eight operators, gates including `gate_axis_mutate`.
2. **Operator inventory** — FREEZEDRY, RESEED, SCHEMAX, SIGNFLIP, SATEXTEND, HISTSWAP, TRAJSWAP, VARSCALE.
3. **Adapter contract** — ZeroDay and AOMB emit twins; DIPTYCH grades; product trees are pinned, not forked into this repo.
4. **Coverage matrix** — Operator × `{diptych_core, zeroday, aomb}`; green iff twin contrast **and** axis power.

**Non-claims (stated up front).** Localization ≠ exploitability. No exploit/PoC payloads. No AUROC / model-score fields in graded envelopes. Adapter greens cite pinned merge SHAs and adapter QCs only—never invented model scores.

---

## II. Related Work and Hyperproperty Taxonomy

### A. Hyperproperties and *k*-safety

Hyperproperties [Clarkson & Schneider] classify requirements over *sets* of executions. **2-safety** hyperproperties are falsified by a finite pair of traces. Relational verification, product programs, and hyperproperty runtime verification study the same shape: couple two runs, then check a relational assertion. DIPTYCH does not claim a new hyperproperty calculus; it supplies an **executable grading harness** and operator taxonomy tuned to agent-authored control artifacts.

### B. Coupling disciplines

| Discipline | Shared exogenous mass | Graded axes (this work) |
|---|---|---|
| **Open-loop** | Shared prefix / cassette / freeze mask | FREEZEDRY, RESEED, SCHEMAX, SIGNFLIP, SATEXTEND, HISTSWAP |
| **CRN closed-loop** | Shared CRN stream + closed-loop residual | TRAJSWAP, VARSCALE |

Common Random Numbers (CRN) couple stochastic closed-loop rollouts so residuals and variance scales are attributable to the declared axis rather than to independent noise draws.

### C. Taxonomy used by DIPTYCH

| Layer | Question | DIPTYCH answer |
|---|---|---|
| Trace property | Does one film satisfy a local invariant? | Necessary but insufficient for calibration |
| 2-safety hyperproperty | Do coupled twins respect the axis? | Graded via conforming/violating pairs |
| Power-on-axis | Does mutating *only* the axis flip pass→fail? | `gate_axis_mutate` |
| Incomparability | Are twins even comparable? | Discard / `inconclusive` (≠ green) |

Verdict vocabulary is exactly `{pass, fail, inconclusive}`. `inconclusive` requires a documented reason and **does not** paint a coverage cell green.

---

## III. DIPTYCH Model: Harness and `gate_axis_mutate`

### A. Shared prefix, suffix probes

DIPTYCH forks a shared prefix—open-loop shape or CRN closed-loop point—then probes **suffixes** under one operator. Pairs that violate the shared-prefix / CRN identity rule are incomparable and are not scored as green.

### B. Schema `0.2` envelope

Hard keys (contract): `diptych_schema`, `source`, `operator`, `coupling`, `probe_id`, `control_role`, `traces` (length ≥ 2), `expected_verdict`.

```json
{
  "diptych_schema": "0.2",
  "source": "diptych_core",
  "operator": "<ONE OF EIGHT>",
  "coupling": "open_loop | crn_closed_loop",
  "probe_id": "string",
  "control_role": "conforming | violating",
  "traces": [
    { "trace_id": "a", "events": [], "channels": {}, "meta": {} },
    { "trace_id": "b", "events": [], "channels": {}, "meta": {} }
  ],
  "expected_verdict": "pass | fail | inconclusive"
}
```

Sources: `{diptych_core, zeroday, aomb}`. TRAJSWAP and VARSCALE **must** use `coupling: crn_closed_loop`. Forbidden fields include `auroc`, `AUROC`, `lab_auroc`, `model_grade` (contract rejection).

### C. Gates

| Gate | Role |
|---|---|
| **Manifest** | 8 operators × `{conforming, violating}` probe paths present |
| **Stub rejection** | Reject TODO / NotImplemented / empty traces / role↔verdict mismatch |
| **Contrast** | Twins not identical; expected verdicts asymmetric (`pass` vs `fail`) |
| **Axis presence** | Operator-specific graded channels/meta present |
| **`gate_axis_mutate`** | On conforming twin: mutate **only** the operator axis → grader must flip `pass`→`fail`; cosmetic edits (verdict-only, SARIF level rename, AUROC inject) leave the axis fingerprint unchanged and **fail** the gate |

A coverage cell is **green** only when (1) conforming → `pass`, (2) violating → `fail`, and (3) `gate_axis_mutate` reports `power_ok`.

### D. Layout (artifact)

```
diptych/           # contract, grade, gates, mutate_axis, CRN helpers
ops/<op>/          # spec.yaml + operator.py
controls/<op>/     # conforming.py + violating.py
diptych-probes/    # full-8 fixture twins (source=diptych_core)
coverage/matrix.json
adapters/PINS.md   # ZeroDay / AOMB pins (read-only)
scripts/run_poc.sh
```

---

## IV. Eight-Operator Table

Waves follow the harness inventory (A: fastest open-loop power; B: polarity/bounds/history; C: CRN closed-loop).

| Op | Wave | Coupling | Primary graded axis | Conforming (→ pass) | Violating (→ fail) | `gate_axis_mutate` (power) |
|---|---|---|---|---|---|---|
| **FREEZEDRY** | A | `open_loop` | `freeze_channels`, `decision_fingerprint`, graded series | rng/clock frozen → identical fingerprints / series | unfrozen leak → diverge | clear freeze + diverge graded + fingerprint |
| **RESEED** | A | `open_loop` | `meta.seed`, `channels.stability`, `epsilon` | seeds differ; L∞ ≤ ε | seeds differ; stability > ε | inflate stability on twin B beyond ε |
| **SCHEMAX** | A | `open_loop` | `channels.schema.keys` / required key set | required key sets equal | required key renamed/dropped | rename/drop required key on twin B |
| **SIGNFLIP** | B | `open_loop` | `signflip_channel` values + sign-normalized fingerprint | odd-symmetry / invariant holds | polarity assumption broken | replace −a with +a; break normalized fingerprint |
| **SATEXTEND** | B | `open_loop` | `sat_lo`/`sat_hi`, clipped channel values | values in sat and legal band | sat pushes into violation | push value above `legal_hi` |
| **HISTSWAP** | B | `open_loop` | history / `hist_splice_at`, alt_history cross-link | splice preserves property | corrupt/misalign history | corrupt at splice; break cross-link |
| **TRAJSWAP** | C | **`crn_closed_loop`** | trajectory swap + `closed_loop_residual` | CRN residual in bound after swap | swap breaks closed-loop invariant | offset trajectory; blow residual past bound |
| **VARSCALE** | C | **`crn_closed_loop`** | `var_scale`, mean-matched `variance_proxy` | scale within bound; ordering holds | scale breaks bound/ordering | raise `var_scale` past bound (**not** AUROC) |

Canonical semantics: [`docs/adapters/OPERATOR_TABLE.md`](../docs/adapters/OPERATOR_TABLE.md), [`docs/OPERATORS.md`](../docs/OPERATORS.md).

---

## V. Methods

### A. Offline PoC protocol

No GPU. No network required for the core PoC. From repository root:

```bash
./scripts/run_poc.sh
# equivalent: make poc
```

The script runs `python3 -m diptych.run_full8` (manifest → stubs → contrast → axis → `gate_axis_mutate` → write `coverage/matrix.json`) then unit tests. Exit code `0` means all eight `diptych_core` cells are green.

### B. Graders

Each operator has a substantive grader in `diptych/grade.py` (no stub/`return True` graders). CRN helpers in `diptych/crn.py` reconstruct shared-noise twins for TRAJSWAP/VARSCALE proofs. Controls under `controls/<op>/` and fixtures under `diptych-probes/<op>/{conforming,violating}/probe.json` provide the twin contrast.

### C. Adapter pins (read-only; not vendored)

| Adapter | Full SHA | Short | Role |
|---|---|---|---|
| **ZeroDay** | `fb5b39daf88e37521aaee8526ae9d286cf74f341` | `fb5b39da` | Emit schema-0.2 twins; DIPTYCH grades |
| **AOMB** | `667e47538ae5b9c504187b7a73220d22aa8fb96f` | `667e475` | Emit schema-0.2 twins; DIPTYCH grades |

Source of truth: [`adapters/PINS.md`](../adapters/PINS.md). This draft does **not** modify ZeroDay or AOMB product trees.

### D. What we refuse to report

- AUROC, lab AUROC, or invented model grades as hyperproperty scores  
- Exploit / attack / PoC payload fields  
- Green adapter cells without pin/merge evidence (ZeroDay #41+#42, AOMB #18) 

---

## VI. Evaluation

### A. Setup

Artifact tip evaluated for this draft’s live matrix: repository `pandeyaby/DIPTYCH`, schema `0.2`, source row `diptych_core`. Matrix path: [`coverage/matrix.json`](../coverage/matrix.json). Notes field in that file: *diptych_core=green requires twin conf/viol AND gate_axis_mutate power-on-axis; zeroday=green and aomb=green at pins ZeroDay@fb5b39daf88e37521aaee8526ae9d286cf74f341 (merged #41+#42) and AOMB@667e47538ae5b9c504187b7a73220d22aa8fb96f (merged #18); no AUROC / invented model scores*.

### B. Live coverage matrix (verbatim from `coverage/matrix.json`)

Operator × `{diptych_core, zeroday, aomb}` with `axis_power`. Adapter greens cite merge facts only: ZeroDay pin = merge of [#41](https://github.com/pandeyaby/ZERODAY/pull/41)+[#42](https://github.com/pandeyaby/ZERODAY/pull/42); AOMB pin = merge of [#18](https://github.com/pandeyaby/AOMB/pull/18); plus adapter green QCs already performed on those pins.

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

**Summary:** live **green×8×3** across `diptych_core`, `zeroday`, and `aomb`, with axis power on every operator. No AUROC column exists. No invented model scores.

### C. Negative controls (repo tests)

Unit tests assert that cosmetic mutators—expected-verdict-only flips, SARIF level renames, AUROC injection—do **not** change the axis fingerprint and therefore fail `gate_axis_mutate`. Missing violating twins and identical twins fail manifest/contrast gates.

### D. Reproducibility command

```bash
./scripts/run_poc.sh   # expect exit 0; refreshes coverage/matrix.json
```

---

## VII. Threats to Validity

**Internal.** Fixture twins are handwritten controls in-repo; they establish protocol power, not statistical generalization across production fleets. Graders are deterministic functions of envelope fields—bugs in graders would systematically mislabel cells.

**Construct.** “Green” means twin contrast + axis power under schema `0.2`, not exploitability, not deployment safety certification, and not model quality. Mapping product domains (SARIF decisions, ranking sessions) onto channels is adapter responsibility; a weak domain mapping could pass DIPTYCH while missing the intended product risk.

**External.** Adapter greens are pinned to ZeroDay@`fb5b39da` (merged #41+#42) and AOMB@`667e475` (merged #18) plus prior adapter QCs; they are not re-derived by vendoring product trees into this repo. A pin move without a matching QC would drop those columns out of green.

**Conclusion validity.** We do not claim AUROC improvements, ranking lifts, or security exploit findings. Claims are limited to harness behavior, merge/pin facts, and the live matrix cells cited above.

---

## VIII. Limitations, Ethics, and Non-Claims

- **Localization ≠ exploitability.** DIPTYCH grades hyperproperty twins; it does not ship attack PoCs.  
- **No AUROC / model scores** in graded envelopes; contract rejects those fields.  
- **`inconclusive` ≠ green.**  
- **Pins only:** ZeroDay and AOMB product trees are not vendored or edited here.  
- **Ethics:** desk-safe fingerprints and fixtures; omit exploit/payload fields from artifacts.

---

## IX. Conclusion

DIPTYCH treats calibration of agent-authored controllers as a **2-safety** grading problem over coupled traces. The harness couples prefixes, applies one of eight operators, requires asymmetric twin verdicts, and demands `gate_axis_mutate` power-on-axis. Live offline evaluation paints a full **green×8×3** matrix—`diptych_core` plus ZeroDay@`fb5b39da` (merged #41+#42) and AOMB@`667e475` (merged #18)—with axis power on every operator. One film can be legal; only the pair is calibrated.

---

## Appendix A — Artifact checklist

| Item | Location |
|---|---|
| Thesis / outline | `PAPER_OUTLINE.md` |
| This draft | `drafts/ieee-draft.md` |
| IEEE skeleton | `drafts/ieee-outline.md` |
| Live matrix | `coverage/matrix.json` |
| Pins | `adapters/PINS.md` |
| PoC | `./scripts/run_poc.sh` / `make poc` |
| Contract / gating | `docs/adapters/CONTRACT.md`, `GATING.md` |

## Appendix B — Citation policy for metrics

Cite only: repository facts, CI exit codes, and cells written to `coverage/matrix.json`. Do not invent AUROC, accuracy, or model-score numbers for this draft.
