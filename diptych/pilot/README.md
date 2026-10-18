# Pilot study

Paired vs. single-trace grading on controllers written by models, on two
tasks. Paper: §II, §VIII-D.

| Task | Requirements come from | Files |
|------|------------------------|-------|
| `concurrency` | Written for this paper (`TASK.md`) | this directory |
| `congestion` | RFC 9743 evaluation criteria, with thresholds we added (`congestion/TASK.md`) | [`congestion/`](congestion/) |

## One pipeline

Probes only **record**: they fork a running controller (or simulation) at a
shared state, drive two branches that differ on one axis, and write the pair
into a *live envelope* ([`diptych/live.py`](../live.py)), the same schema-0.2
shape the control fixtures and adapters use, minus the expected verdict.
Grading is the shared path in `diptych.grade`:

    validate -> comparability (twins present, equal-length channels, CRN stream)
             -> requirement predicate -> pass | fail | inconclusive

Predicates (`predicates.py`, `congestion/predicates.py`) read only the
envelope, so saved envelopes re-grade offline to the verdicts the study
reported:

```bash
PILOT_ENVELOPE_DIR=/tmp/env python -m diptych.pilot.study --controls-only
diptych-grade --input /tmp/env
```

Requirements that are single-run properties (the congestion task has three)
are graded by single-run checks, not envelopes.

## Files

| File | Role |
|------|------|
| `tasks.py` | What differs between tasks: text, controls, workspace, result file |
| `TASK.md`, `congestion/TASK.md` | Task text given to the models unchanged |
| `plant.py`, `congestion/plant.py` | Deterministic simulators; per-tick noise pre-drawn from the seed (CRN) |
| `congestion/reference.py` | The "standard" algorithm (Reno-style AIMD) used as reference and competitor |
| `controls/`, `congestion/controls/` | Executable conforming control and violators, one design decision each |
| `probes.py`, `congestion/probes.py` | Recorders, single-trace baselines (basic and strong), `evaluate()` |
| `predicates.py`, `congestion/predicates.py` | Requirement predicates over live envelopes |
| `worker.py` | Fresh-interpreter runner (isolation; cross-process probes) |
| `generate.py` | Generates controllers: one-shot or agentic, Claude or Codex |
| `agent_sim.py`, `congestion/agent_sim.py` | Simulator copied into agent workspaces as `sim.py` (no probes) |
| `artifacts/` | Generated controllers + `manifest.json` (task, condition, agent, model, cost or tokens) |
| `study.py` | Grades everything; writes `results.json` / `results_congestion.json` |

## Reproduce (no model calls)

```bash
python -m diptych.pilot.study                    # concurrency task
python -m diptych.pilot.study --task congestion  # congestion task
```

## Regenerate controllers (costs money)

```bash
python -m diptych.pilot.generate --task congestion --condition oneshot --agent claude
python -m diptych.pilot.generate --task congestion --condition agentic --agent codex
```

Agents in the agentic condition get the task text, the simulator, and (for
congestion) the standard algorithm; they never see probes, predicates, or
controls.

## Scope

Five samples per model and condition; two tasks. On the concurrency task we
wrote the requirements; on the congestion task they paraphrase RFC 9743 but
the thresholds, simulator, and baselines are ours. Not a model or vendor
comparison.
