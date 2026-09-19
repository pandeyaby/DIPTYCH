# DIPTYCH

**DIPTYCH** grades calibration as **2-safety hyperproperties**: not one run, but a
coupled pair sharing all exogenous inputs except one controlled perturbation. It
forks a shared prefix (open-loop shape / CRN closed-loop point), discards
incomparable pairs, and probes suffixes only.

> One film = legal; only the pair = calibrated.

IEEE artifact harness. Schema `0.2`. Eight operators + `gate_axis_mutate`.

## Quick PoC

```bash
./scripts/run_poc.sh
# or: make poc
```

Exit `0` means full-8 `diptych_core` cells are green (twin contrast + axis power).
See [`examples/poc/`](examples/poc/) and [`PAPER_OUTLINE.md`](PAPER_OUTLINE.md).

## Paper

- [`PAPER_OUTLINE.md`](PAPER_OUTLINE.md) — thesis, contributions, section plan
- [`drafts/ieee-outline.md`](drafts/ieee-outline.md) — IEEE draft skeleton

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
diptych/           # core package (contract, grade, gates, axis mutate)
ops/<op>/          # spec.yaml + operator.py
controls/<op>/     # conforming.py + violating.py
diptych-probes/    # full-8 fixture twins (source=diptych_core)
coverage/matrix.json
docs/adapters/     # CONTRACT, GATING, ONEPAGER, zeroday, aomb
examples/poc/
scripts/run_poc.sh
PAPER_OUTLINE.md
drafts/ieee-outline.md
```

## Non-claims

- No AUROC / model-score fields in graded envelopes
- No exploit / PoC payloads
- Localization ≠ exploitability
- `inconclusive` ≠ green

## License

See [`LICENSE`](LICENSE) (MIT).
