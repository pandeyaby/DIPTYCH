# Witness / axis-power recipes (reviewer audit trail)

Harness-only. Product trees (ZeroDay / AOMB) are **not** vendored here.
Reviewers can audit green cells without opening those repos by checking:

1. Twin contrast + `gate_axis_mutate` recipes below  
2. Gating rules in [`GATING.md`](GATING.md)  
3. Live cells in `coverage/matrix.json` at pins in `adapters/PINS.md`

**Non-claims:** these recipes establish *probe power* (the axis can flip
pass→fail). They are **not** vulnerability findings, AUROC, accuracy, or
model ranks. `inconclusive` ≠ green.

---

## Shared gate recipe (`gate_axis_mutate`)

Implementation: `diptych/gates.py` → `gate_axis_mutate(op, conf)`.

| Step | Requirement |
|------|-------------|
| Baseline | Conforming envelope grades `pass` |
| Mutate | Change **only** that operator’s axis payload (`mutate_axis`) |
| Detect | Axis fingerprint must change (verdict-only / cosmetic edits fail) |
| Flip | Mutated envelope grades `fail` |
| Coupling | TRAJSWAP / VARSCALE must keep `crn_closed_loop` |

Cosmetic mutators (e.g. rewrite `expected_verdict` only) must **fail** the
gate — covered by unit tests in `tests/test_full8.py`.

---

## Per-operator witnesses (harness domain)

Aligned with [`OPERATOR_TABLE.md`](OPERATOR_TABLE.md) and `ops/*/spec.yaml`.
Controls live under `controls/<OP>/`; fixtures under `diptych-probes/<OP>/`.

| Op | Coupling | Valid axis witness | Not a witness |
|----|----------|--------------------|---------------|
| **FREEZEDRY** | `open_loop` | Freeze-mask / fingerprint channels diverge when unfrozen | Renaming labels without thaw |
| **RESEED** | `open_loop` | `meta.seed` change moves stability beyond ε | Seed field present but unused |
| **SCHEMAX** | `open_loop` | Required schema key set equality breaks (rename/drop) | Extra unused keys only |
| **SIGNFLIP** | `open_loop` | Signed polarity / `score_margin` (top1−top2) odd-symmetry | SARIF level note↔error rename |
| **SATEXTEND** | `open_loop` | Saturation bounds extended into a property violation | Cosmetic bound comments |
| **HISTSWAP** | `open_loop` | History splice corrupts the graded invariant | Reordering unrelated metadata |
| **TRAJSWAP** | `crn_closed_loop` | Trajectory segment swap + nonempty `closed_loop_residual` | `open_loop`; empty residual |
| **VARSCALE** | `crn_closed_loop` | Mean-matched `var_scale` / `variance_proxy` breaks bound | AUROC; scaling ranks as “variance” |

---

## ZeroDay deferred-uplift witnesses (adapter domain)

Detail also summarized in [`zeroday.md`](zeroday.md) and
[`CONTRACT.md`](CONTRACT.md). Pins only (`adapters/PINS.md`); no product edits.

| Op | Valid ZeroDay-domain witness | Not allowed |
|----|------------------------------|-------------|
| **SIGNFLIP** | Signed continuous `channels.score_margin.values` = score(top1)−score(top2) | SARIF level renames; negating positive ranks only |
| **TRAJSWAP** | locate→verify CRN with nonempty `closed_loop_residual`; mid-horizon segment swap | `open_loop`; rankedFiles permute with empty residual |
| **VARSCALE** | `meta.var_scale` on exploration noise; `variance_proxy` at **mean-matched** finding count | AUROC; scaling result counts as variance |

Optional `freeze_channels`: SIGNFLIP may share `["clock"]`; TRAJSWAP may freeze
`["rng","clock"]` so residual is trajectory-attributable; VARSCALE must **not**
freeze the scaled noise source.

---

## AOMB witness notes

See [`aomb.md`](aomb.md). Full-8 fixtures under schema `0.2`; lab AUROC stays
unpublished / never a graded channel. Ranking-smoke narratives are **not**
coverage greens.

---

## How reviewers check without product repos

```bash
./scripts/run_poc.sh          # exit 0 ⇒ green×8×3 matrix refresh
PYTHONPATH=. python -m pytest -q
# inspect: coverage/matrix.json · adapters/PINS.md · docs/adapters/GATING.md
```

Paper pointer: [`paper/WITNESSES.md`](../../paper/WITNESSES.md) and Appendix
in `paper/one-trace-is-not-enough.tex`.
