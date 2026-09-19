# PoC — full-8 DIPTYCH gate

Deterministic offline smoke. No GPU. No network. No AUROC.

```bash
# from repo root
./scripts/run_poc.sh
# or
make poc
```

Expected: exit 0, `coverage/matrix.json` with green×8×3
(`diptych_core` / `zeroday` / `aomb`), and `gate_axis_mutate` power-on-axis
true for each operator. Adapter greens cite ZeroDay@fb5b39da (merged #41+#42)
and AOMB@667e475 (merged #18)—no AUROC / invented scores.
