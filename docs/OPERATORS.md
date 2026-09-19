# DIPTYCH Operator Inventory (bootstrap from mandate)

Paper: *One Trace Is Not Enough: Hyperproperty Grading for Agent-Authored Control Systems* (DIPTYCH harness).

**Status:** No local paper PDF or existing repo found on first run. Specs below are executable *bootstrap contracts* keyed to the eight named operators. Replace/align field-by-field with paper tables when the draft lands. Do **not** invent evaluation scores.

## Hyperproperty grading (harness concepts)

| Concept | Role |
|---|---|
| Trace pair / probe set | Grade over ≥2 related executions, not a single trajectory |
| Coupling discipline | Open-loop probes + CRN closed-loop probes |
| Comparability horizon | Window over which paired traces remain comparable |
| Probe-tree amortization | Share prefixes across probe branches (cost model) |
| Verdicts | `pass` / `fail` / `inconclusive` (never invent scores) |

## Eight operators (implementation order)

### Wave A — fastest power (implement first)
1. **FREEZEDRY** — freeze a subset of state/input channels across the paired probe so dynamics remain comparable under controlled stasis.
2. **RESEED** — reseed stochastic / RNG / initial-condition channels while holding the rest of the coupling fixed.
3. **SCHEMAX** — perturb discrete schedule / mode / phase indexing (schema of time or event slots).

### Wave B
4. **SIGNFLIP** — flip sign / polarity of a designated continuous channel (control or observation).
5. **SATEXTEND** — extend saturation / clip bounds or activate saturation earlier/later on a channel.
6. **HISTSWAP** — swap or splice history buffers / delay lines between paired traces.

### Wave C — closed-loop
7. **TRAJSWAP** — swap trajectory segments (or reference paths) between coupled runs under CRN closed-loop.
8. **VARSCALE** — scale variance / gain / noise intensity on a designated channel under closed-loop coupling.

## Per-operator deliverable (executable)

For each operator `OP`:
- `ops/<op>/spec.yaml` — parameters, coupling mode (`open_loop` | `crn_closed_loop`), horizon, inconclusive conditions
- `ops/<op>/operator.py` — pure transform `(trace_a, trace_b, params) -> probe_pair`
- `controls/<op>/conforming.py` — handwritten control that should **pass** the hyperproperty under `OP`
- `controls/<op>/violating.py` — handwritten control that should **fail** under `OP`
- Unit tests that assert verdicts on the handwritten controls only (protocol evidence, not model scores)

## Non-claims
- No fabricated model-study metrics
- Do not fork ZeroDay or AOMB product code; define adapter APIs only
- Quote any cloud GPU spend before use; Abhinav approval required
