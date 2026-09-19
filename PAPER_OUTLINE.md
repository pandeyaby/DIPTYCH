# PAPER_OUTLINE — DIPTYCH

**Working title:** *One Trace Is Not Enough: Hyperproperty Grading for Agent-Authored Control Systems*

**Venue target:** IEEE (artifact-friendly).

**Living IEEEtran source:** [`paper/one-trace-is-not-enough.tex`](paper/one-trace-is-not-enough.tex)  
**Authors:** Abhishek Pandey (Meta), Abhinav Pandey (Cisco)  
**Full markdown draft:** [`drafts/ieee-draft.md`](drafts/ieee-draft.md)  
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

## Non-claims

- Localization ≠ exploitability. No PoC/exploit payloads.
- No AUROC / model-score fields in graded envelopes.
- Adapters do not fork product trees into this repo; they pin SHAs.

## Outline → IEEEtran map (`paper/one-trace-is-not-enough.tex`)

| Section | TeX |
|---------|-----|
| 1. Introduction | §I |
| 2. Hyperproperty framing + related work | §II |
| 3. DIPTYCH model | §III |
| 4. Operator semantics | §IV |
| 5. Harness: schema, gates, axis mutate | §V |
| 6. Adapters: ZeroDay@fb5b39da, AOMB@667e475 | §VI |
| 7. **Evaluation protocol** (no invented scores) | **§VII** |
| 8. Limitations & ethics | §VIII |
| 9. Conclusion | §IX |

## Artifact

```bash
./scripts/run_poc.sh   # exit 0
```

Pins: **ZeroDay@fb5b39da** · **AOMB@667e475**  
Live matrix: [`coverage/matrix.json`](coverage/matrix.json) (green×8×3: `diptych_core` / `zeroday` / `aomb`; ZeroDay@fb5b39da = merged #41+#42; AOMB@667e475 = merged #18).
