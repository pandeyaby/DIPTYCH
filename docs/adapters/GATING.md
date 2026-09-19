# How DIPTYCH gates thin implementations

1. **Manifest gate:** `tests/test_full8_manifest.py` — every source in `{diptych_core,zeroday,aomb}` must declare 8 operators × {conforming,violating} artifact paths.
2. **Contrast gate:** for each pair, run harness; require asymmetric verdicts (pass vs fail) unless both honestly `inconclusive` with documented reason (inconclusive≠green).
3. **Axis gate:** mutating the operator axis on the conforming fixture must be able to flip the verdict (power); identical twins → fail the gate.
4. **Stub detectors:** reject strings/markers `TODO`, `NotImplemented`, `stub`, empty traces, `expected_verdict` hardcoded without running grader.
5. **PR policy:** DIPTYCH flags product PRs that only smoke 1–2 operators; GRAX informed; coverage matrix stays non-green.

Canonical contract paths in this harness repo:
- `docs/adapters/CONTRACT.md`
- `docs/adapters/GATING.md` (this file)
- `docs/adapters/zeroday.md`
- `docs/adapters/aomb.md`
- `docs/adapters/OPERATOR_TABLE.md` / `ONEPAGER.md`
- `docs/OPERATORS.md`

**Non-claims for paper / matrix reporting:** no fabricated LLM or AUROC scores;
protocol + handwritten controls first; model study in progress;
`inconclusive` ≠ green; adapter greens only at pinned SHAs
(`adapters/PINS.md`: ZeroDay@fb5b39da, AOMB@667e475).
