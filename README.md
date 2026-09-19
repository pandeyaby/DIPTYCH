# DIPTYCH

[![CI](https://github.com/pandeyaby/DIPTYCH/actions/workflows/ci.yml/badge.svg)](https://github.com/pandeyaby/DIPTYCH/actions/workflows/ci.yml)

**DIPTYCH** grades calibration as **2-safety hyperproperties**: not one run, but a
coupled pair sharing all exogenous inputs except one controlled perturbation. It
forks a shared prefix (open-loop shape / CRN closed-loop point), discards
incomparable pairs, and probes suffixes only.

> One film = legal; only the pair = calibrated.

IEEE artifact harness. Schema `0.2`. Eight operators + `gate_axis_mutate`.

![One film vs coupled diptych](docs/images/diptych-vs-single-trace.png)

![ZeroDay and AOMB feed DIPTYCH graders](docs/images/stack.png)

## Quick PoC

```bash
./scripts/run_poc.sh
# or: make poc
```

Exit `0` means full-8 `diptych_core` cells are green (twin contrast + axis power).
See [`examples/poc/`](examples/poc/).

## Paper

Living IEEEtran conference source (authors: Abhishek Pandey / Meta, Abhinav Pandey / Cisco):

- **[`paper/one-trace-is-not-enough.tex`](paper/one-trace-is-not-enough.tex)** — authoritative IEEEtran source (§VII = evaluation **protocol**; ZeroDay@`fb5b39da` / AOMB@`667e475`; Table `tab:coverage` = live green×8×3; no invented scores)
- [`paper/ARTIFACT_CHECKLIST.md`](paper/ARTIFACT_CHECKLIST.md) — IEEE artifact checklist (code, controls, logs, non-claims, reproduce)
- [`paper/README.md`](paper/README.md) — compile notes + figure paths
- [`drafts/ieee-draft.md`](drafts/ieee-draft.md) — markdown prose draft (must not contradict `.tex`)
- [`PAPER_OUTLINE.md`](PAPER_OUTLINE.md) — thesis / section plan

## Why no AUROC

AUROC (and lab AUROC / model grades) are **single-trace ranking scores**. DIPTYCH grades a **2-safety** claim over a *coupled pair*: twin contrast + `gate_axis_mutate` power-on-axis. Publishing AUROC as a hyperproperty grade would:

1. Collapse a relational property into a unary model metric
2. Invite cosmetic score fields that fail the axis gate (contract rejects `auroc` / `model_grade`)
3. Confuse localization with exploitability or model quality

Coverage cells record categorical green/pending status and boolean `axis_power` only — never invented model scores. See paper §VII (evaluation protocol).

## Operators

| Wave | Operators | Coupling |
|------|-----------|----------|
| A | FREEZEDRY, RESEED, SCHEMAX | open_loop |
| B | SIGNFLIP, SATEXTEND, HISTSWAP | open_loop |
| C | TRAJSWAP, VARSCALE | **crn_closed_loop** |

Specs: [`docs/OPERATORS.md`](docs/OPERATORS.md) · [`docs/adapters/`](docs/adapters/)

## Adapter pins

Product emitters are **not** vendored here. Pin and grade:

| Adapter | Short SHA | Full |
|---------|-----------|------|
| **ZeroDay** | `fb5b39da` | `fb5b39daf88e37521aaee8526ae9d286cf74f341` |
| **AOMB** | `667e475` | `667e47538ae5b9c504187b7a73220d22aa8fb96f` |

Details: [`adapters/PINS.md`](adapters/PINS.md).

## Layout

```
paper/             # living IEEEtran source
diptych/           # core package (contract, grade, gates, axis mutate)
ops/<op>/          # spec.yaml + operator.py
controls/<op>/     # conforming.py + violating.py
diptych-probes/    # full-8 fixture twins (source=diptych_core)
coverage/matrix.json
docs/images/       # diptych-vs-single-trace + stack diagrams
docs/adapters/     # CONTRACT, GATING, ONEPAGER, zeroday, aomb
examples/poc/
scripts/run_poc.sh
PAPER_OUTLINE.md
drafts/ieee-draft.md
.github/workflows/ci.yml
```

## Non-claims

- No AUROC / model-score fields in graded envelopes
- No exploit / PoC payloads
- Localization ≠ exploitability
- `inconclusive` ≠ green

## License

See [`LICENSE`](LICENSE) (MIT).
