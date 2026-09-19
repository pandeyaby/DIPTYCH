# DIPTYCH adapter contracts (v0.2 — full-8 gate)

**Escalation (GRAX / Abhinav):** ZeroDay and AOMB each implement **all 8** operators as substantive product probes — not stubs, not a 2-operator slice.

Operators (every one, real conforming + violating controls):
`SIGNFLIP`, `TRAJSWAP`, `VARSCALE`, `SATEXTEND`, `HISTSWAP`, `FREEZEDRY`, `RESEED`, `SCHEMAX`.

Canonical semantics live in DIPTYCH. Product agents emit probe pairs; DIPTYCH grades.

## Common probe-pair envelope

```json
{
  "diptych_schema": "0.2",
  "source": "zeroday" | "aomb",
  "operator": "<ONE OF EIGHT>",
  "coupling": "open_loop" | "crn_closed_loop",
  "horizon": { "unit": "steps" | "ms" | "events", "length": 0 },
  "probe_id": "string",
  "fixture_id": "string",
  "control_role": "conforming" | "violating",
  "traces": [
    { "trace_id": "a", "events": [], "channels": {}, "meta": {} },
    { "trace_id": "b", "events": [], "channels": {}, "meta": {} }
  ],
  "expected_verdict": "pass" | "fail" | "inconclusive"
}
```

Hard keys: `diptych_schema`, `source`, `operator`, `coupling`, `probe_id`, `control_role`, `traces` (len≥2), `expected_verdict`.
Do **not** invent score fields (`auroc`, model grades). DIPTYCH computes grades.

### Coupling requirements
| Operators | Default coupling |
|---|---|
| FREEZEDRY, RESEED, SCHEMAX, SIGNFLIP, SATEXTEND, HISTSWAP | `open_loop` OK |
| TRAJSWAP, VARSCALE | **must** be `crn_closed_loop` |

## Per-operator contrast (thin-impl rejection)

Each operator artifact **must** ship a conforming + violating pair that differs *only* in the hyperproperty-relevant axis. CI fails if:

1. Operator missing from coverage manifest
2. Only one of `{conforming, violating}` present
3. Both controls yield the same expected_verdict
4. `TODO`, `stub`, `not_implemented`, hardcoded `pass`, or empty `traces`
5. Smoke-only check with no paired contrast (single trace / identical twins with no axis change)
6. For TRAJSWAP/VARSCALE: `coupling != crn_closed_loop`

### Operator axes (canonical)
| Op | Perturbation axis | Conforming contrast | Violating contrast |
|---|---|---|---|
| FREEZEDRY | freeze mask / serialize-restore | bit-identical graded channels | unfrozen nondeterminism → diverge |
| RESEED | `meta.seed` only | stability channel within ε | out-of-ε / seed-leaking policy |
| SCHEMAX | schema key set | required keys equal | required key rename/drop |
| SIGNFLIP | sign/polarity of named channel | invariant holds under flip | broken polarity assumption |
| SATEXTEND | saturation/clip bounds | bound-respecting behavior | saturates into violation |
| HISTSWAP | history/delay buffer splice | hyperproperty holds after splice | history corruption breaks it |
| TRAJSWAP | trajectory/reference segment swap (CRN) | closed-loop invariant holds | swap breaks closed-loop property |
| VARSCALE | variance/gain/noise scale (CRN) | mean-matched scale within bound | scale breaks ordering/bound |

## Coverage matrix (green rule)

Path: `coverage/matrix.json` (DIPTYCH) + product CI mirrors.

```json
{
  "diptych_schema": "0.2",
  "operators": {
    "FREEZEDRY": { "diptych_core": "pending", "zeroday": "pending", "aomb": "pending" },
    "RESEED": { "diptych_core": "pending", "zeroday": "pending", "aomb": "pending" },
    "SCHEMAX": { "diptych_core": "pending", "zeroday": "pending", "aomb": "pending" },
    "SIGNFLIP": { "diptych_core": "pending", "zeroday": "pending", "aomb": "pending" },
    "SATEXTEND": { "diptych_core": "pending", "zeroday": "pending", "aomb": "pending" },
    "HISTSWAP": { "diptych_core": "pending", "zeroday": "pending", "aomb": "pending" },
    "TRAJSWAP": { "diptych_core": "pending", "zeroday": "pending", "aomb": "pending" },
    "VARSCALE": { "diptych_core": "pending", "zeroday": "pending", "aomb": "pending" }
  }
}
```

Cell is **green** only when: conforming control → `pass`, violating → `fail`, and grader exercises the named axis (power check). `pending` / `stub` / `smoke` ≠ green.

## Gate implementation (DIPTYCH)

- `diptych.gates.full8_manifest` — requires exactly 8 operators × 2 roles
- `diptych.gates.contrast_power` — asserts verdict asymmetry + axis mutation detected
- CI job `adapter-gate` fails PR if any operator skipped/stubbed
- Review rule: flag any PR with TODO operator, hardcoded pass, or missing violating twin


## ZeroDay deferred-uplift witnesses (SIGNFLIP / TRAJSWAP / VARSCALE)

See `WITNESSES.md` + `zeroday.md`. Summary:

- **SIGNFLIP:** signed continuous `score_margin` (top1−top2), not SARIF level renames.
- **TRAJSWAP:** locate→verify CRN with nonempty `closed_loop_residual`; segment swap power.
- **VARSCALE:** mean-matched `var_scale` + `variance_proxy`; never AUROC/rank-as-variance.

## Product binding notes
- **ZeroDay:** cassettes / serialize-restore / SARIF+decision fingerprints for all 8 (desk-safe; no exploit/PoC fields)
- **AOMB:** fixtures / ranking-smoke path for all 8; lab AUROC stays `not_published`
