# Pilot study

Paired vs. single-trace grading on controllers written by models, for a
reduced version of the paper's orchestrator task. Paper: §VIII-D, Table
`tab:pilot`.

| File | Role |
|------|------|
| `TASK.md` | Task given verbatim to the models: interface + eight requirements |
| `plant.py` | Deterministic closed-loop service model; per-tick noise pre-drawn from the seed (CRN) |
| `controls/` | Executable conforming controller and eight violators, one design decision each |
| `probes.py` | Paired probes (fork live controllers, count ticks), single-trace baseline, `evaluate()` |
| `worker.py` | Fresh-interpreter runner (isolation, RESEED/FREEZEDRY across processes) |
| `generate.py` | Headless `claude -p`, tools disabled, one turn per sample |
| `artifacts/` | The 20 generated controllers + `manifest.json` (model, sample, cost) |
| `study.py` | Grades everything, writes `results.json`, prints the tables |

Reproduce the results from the committed controllers (no model calls):

```bash
python -m diptych.pilot.study
```

Regenerate controllers (costs money; the committed set cost $10.07 total):

```bash
python -m diptych.pilot.generate --samples 5
```

Scope: 20 tool-free, single-shot samples from four Claude models; one reduced
task; the single-trace baseline is one reasonable reading per requirement,
calibrated so the conforming control passes. Not a model comparison.
