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
| `generate.py` | Generates controllers: one-shot (`claude -p` tools disabled; `codex exec` read-only) or agentic (workspace with task, plant, `sim.py`) |
| `agent_sim.py` | Closed-loop simulator copied into agent workspaces as `sim.py` (no probes) |
| `artifacts/` | The 40 generated controllers + `manifest.json` (condition, agent, model, cost or tokens) |
| `study.py` | Grades everything, writes `results.json`, prints the tables |

Reproduce the results from the committed controllers (no model calls):

```bash
python -m diptych.pilot.study
```

Regenerate controllers (costs money; the committed set cost $23.14 on
Anthropic plus about 80k output / 2.7M input tokens, mostly cached, on OpenAI):

```bash
python -m diptych.pilot.generate --condition oneshot --agent claude
python -m diptych.pilot.generate --condition agentic --agent claude
python -m diptych.pilot.generate --condition oneshot --agent codex
python -m diptych.pilot.generate --condition agentic --agent codex
```

Scope: 40 controllers, five per model and condition; one reduced task; the
single-trace baseline is one reasonable reading per requirement, calibrated so
the conforming control passes. Not a model or vendor comparison.
