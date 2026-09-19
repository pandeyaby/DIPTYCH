# PoC — full-8 DIPTYCH gate

Deterministic offline smoke. No GPU. No network. No AUROC.

```bash
# from repo root
./scripts/run_poc.sh
# or
make poc
```

Expected: exit 0, `coverage/matrix.json` with all eight `diptych_core` cells `green`,
and `gate_axis_mutate` power-on-axis true for each operator.
