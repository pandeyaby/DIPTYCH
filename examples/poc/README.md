# PoC — full-8 DIPTYCH gate (5-minute stranger path)

Deterministic offline smoke. No GPU. No network. No AUROC / invented model scores.

## Prerequisites

- Git
- **Python ≥ 3.10** (`python3`)
- Core harness is **stdlib-only** (no `pip install` required to run the PoC)
- Optional: `pip install pytest` for CI-parity (`python -m pytest -q`)

## Run

```bash
git clone https://github.com/pandeyaby/DIPTYCH.git
cd DIPTYCH
./scripts/run_poc.sh
# or: make poc
```

## Expected printout (success)

Exit **0**. You should see:

1. Per-operator `diptych_core=green axis_power=True mutate=pass→fail` (×8)
2. `GATE PASS`
3. A **green×8×3** table (`core` / `zeroday` / `aomb` all `green`, `axis_power=True`)
4. `MATRIX CHECK OK` against [`expected_matrix_snippet.json`](expected_matrix_snippet.json)
5. Unit tests `OK` / `PoC OK`

Adapter greens cite ZeroDay@`fb5b39da` (merged #41+#42) and AOMB@`667e475`
(merged #18)—pin/merge facts only, never invented model scores.

## What green×8×3 does **not** claim

Harness coverage + `gate_axis_mutate` axis power only. Not vulnerability-finding,
not accuracy, not AUROC / model quality. See [`paper/ARTIFACT_CHECKLIST.md`](../../paper/ARTIFACT_CHECKLIST.md).
