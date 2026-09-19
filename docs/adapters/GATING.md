# How DIPTYCH gates thin implementations

1. **Manifest gate:** `tests/test_full8_manifest.py` — every source in `{diptych_core,zeroday,aomb}` must declare 8 operators × {conforming,violating} artifact paths.
2. **Contrast gate:** for each pair, run harness; require asymmetric verdicts (pass vs fail) unless both honestly `inconclusive` with documented reason (inconclusive≠green).
3. **Axis gate:** mutating the operator axis on the conforming fixture must be able to flip the verdict (power); identical twins → fail the gate.
4. **Stub detectors:** reject strings/markers `TODO`, `NotImplemented`, `stub`, empty traces, `expected_verdict` hardcoded without running grader.
5. **PR policy:** DIPTYCH flags product PRs that only smoke 1–2 operators; GRAX informed; coverage matrix stays non-green.

Contract paths on DIPTYCH box (canonical until landed in repo):
- `/workspace/diptych-spec/adapters/CONTRACT.md`
- `/workspace/diptych-spec/adapters/GATING.md`
- `/workspace/diptych-spec/adapters/zeroday.md`
- `/workspace/diptych-spec/adapters/aomb.md`
- `/workspace/diptych-spec/OPERATORS.md`
