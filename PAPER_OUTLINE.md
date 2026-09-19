# PAPER_OUTLINE — DIPTYCH

**Working title:** *One Trace Is Not Enough: Hyperproperty Grading for Agent-Authored Control Systems*

**Venue target:** IEEE (artifact-friendly). See also [`drafts/ieee-outline.md`](drafts/ieee-outline.md).

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

## Outline

1. Introduction — single-trace insufficiency
2. Hyperproperty framing (2-safety) and coupling discipline
3. Operator semantics (open-loop vs CRN closed-loop)
4. Harness: schema, gates, axis mutate
5. Adapters: ZeroDay@fb5b39da, AOMB@667e475
6. Artifact evaluation (full-8 PoC)
7. Limitations & ethics
8. Related work
9. Conclusion

## Artifact

```bash
./scripts/run_poc.sh   # exit 0
```

Pins: **ZeroDay@fb5b39da** · **AOMB@667e475**
