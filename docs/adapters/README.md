# DIPTYCH adapter specs (pinned Origin sources)

**Schema:** `diptych_schema="0.2"`  
**Upstream repo:** https://origin.cursor.com/abhinavpandey/tmp-c44b600dec44401a

Local Markdown copies in this directory are offline mirrors for CI/review. When specs drift, prefer the **pinned Origin raw URLs** below.

## Pinned Origin raw URLs (stable)

| Doc | Origin raw URL |
|-----|----------------|
| CONTRACT | https://origin.cursor.com/abhinavpandey/tmp-c44b600dec44401a/raw/main/docs/adapters/CONTRACT.md |
| GATING | https://origin.cursor.com/abhinavpandey/tmp-c44b600dec44401a/raw/main/docs/adapters/GATING.md |
| AOMB binding | https://origin.cursor.com/abhinavpandey/tmp-c44b600dec44401a/raw/main/docs/adapters/aomb.md |
| OPERATORS | https://origin.cursor.com/abhinavpandey/tmp-c44b600dec44401a/raw/main/docs/OPERATORS.md |
| ONEPAGER | https://origin.cursor.com/abhinavpandey/tmp-c44b600dec44401a/raw/main/docs/adapters/ONEPAGER.md |

## Local vendor copies

| Doc | Path |
|-----|------|
| CONTRACT | [`CONTRACT.md`](CONTRACT.md) |
| GATING | [`GATING.md`](GATING.md) |
| WITNESSES | [`WITNESSES.md`](WITNESSES.md) |
| AOMB | [`aomb.md`](aomb.md) |
| ZeroDay | [`zeroday.md`](zeroday.md) |
| OPERATORS | [`OPERATORS.md`](OPERATORS.md) |
| OPERATOR_TABLE | [`OPERATOR_TABLE.md`](OPERATOR_TABLE.md) |
| ONEPAGER | [`ONEPAGER.md`](ONEPAGER.md) |

## Coverage note

Live `coverage/matrix.json` is **green×8×3** (`diptych_core` / `zeroday` /
`aomb`) with `axis_power: true` on every operator, attributed to pins
ZeroDay@`fb5b39da` and AOMB@`667e475` (see `adapters/PINS.md`). No AUROC /
invented model scores. Reproduce: `./scripts/run_poc.sh`.

Product trees are **not** vendored here; adapters emit schema-`0.2` twins and
DIPTYCH grades. IEEE checklist: `paper/ARTIFACT_CHECKLIST.md`.
