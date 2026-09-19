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
| AOMB | [`aomb.md`](aomb.md) |
| OPERATORS | [`OPERATORS.md`](OPERATORS.md) |
| OPERATOR_TABLE | [`OPERATOR_TABLE.md`](OPERATOR_TABLE.md) |
| ONEPAGER | [`ONEPAGER.md`](ONEPAGER.md) |

## Coverage note

DIPTYCH reports `diptych_core` green for all 8 operators. The **aomb** column turns green when this AOMB full-8 adapter PR lands and CI passes (`coverage/matrix.json`).

AOMB emit path: `diptych-probes/<OP>/{conforming,violating}/probe.json` · validator: `adapters/aomb.py` · gate: `./scripts/run_diptych_full8.sh`.
