# Adapter pins

DIPTYCH does not vendor ZeroDay or AOMB product code. Adapters emit schema-0.2
probe twins; this harness grades.

| Adapter | Pin | Repo |
|---------|-----|------|
| ZeroDay | `fb5b39daf88e37521aaee8526ae9d286cf74f341` (**fb5b39da**) | https://github.com/pandeyaby/ZERODAY |
| AOMB | `667e47538ae5b9c504187b7a73220d22aa8fb96f` (**667e475**) | https://github.com/pandeyaby/AOMB |

**Pins frozen as of main `f606cfe4`** (post-#16 merge). Do not bump unless the
coverage matrix / expected PoC snippet requires a new product SHA. Harness-only
PRs must not edit ZeroDay or AOMB product trees.

Docs: [`docs/adapters/`](../docs/adapters/).
