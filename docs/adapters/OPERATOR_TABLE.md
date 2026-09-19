# DIPTYCH operator→contract table (v0.2)

`diptych_schema`: **"0.2"** (bump from 0.1). Hard keys: schema, source, operator, coupling, probe_id, control_role, traces[≥2], expected_verdict.

## Canonical expected_verdict strings
Exactly one of: `pass` | `fail` | `inconclusive`
- conforming control → `pass`
- violating control → `fail`
- `inconclusive` only with `meta.inconclusive_reason`; **does not count as green** in coverage matrix

## Shared cassette conventions (ZeroDay)
- `cassette.format`: `serialize_restore` | `vcr_json` | `none` (synthetic twins without replay)
- `cassette.bytes_or_path`: repo-relative path under CI artifacts, e.g. `diptych-probes/<op>/<role>/cassette.json` or `.bin`
- Prefer path refs over inline blobs. Same cassette bytes for FREEZEDRY twins except freeze/nondet axis.

## Operator table

| Op | coupling | Primary graded channels / fields | Conforming (→ pass) | Violating (→ fail) | ZeroDay cassette notes | AOMB channel sketch |
|---|---|---|---|---|---|---|
| FREEZEDRY | open_loop | `meta.decision_fingerprint`, `channels.sarif_fingerprint.keys`, `meta.freeze_channels` | serialize→restore (or frozen rng/clock) → **identical** fingerprints | omit freeze / leak clock|rng → fingerprints **differ** | format `serialize_restore`; path `…/FREEZEDRY/{conforming,violating}/cassette.*`; freeze_channels e.g. `["rng","clock"]` on conforming only | `channels.stability` unused; emit twin sessions with freeze mask vs without |
| RESEED | open_loop | `meta.seed`, `channels.stability.values` | seeds differ; stability L∞/RMSE ≤ ε | seeds differ; stability exceeds ε (or policy reads seed) | format `none` or shared cassette + different `meta.seed`; path optional | `channels.stability.values` length=horizon; document ε in `meta.epsilon` |
| SCHEMAX | open_loop | `channels.schema.keys` or `meta.required_schema_keys` | required key **sets equal** (order-invariant) | required key renamed/dropped | two fixture scenarios; cassette `none` or two paths; SARIF ruleId set may also be graded if tagged `schema` | OTel/session required keys list; optional keys may differ on conforming |
| SIGNFLIP | open_loop | `channels.<target>.values` + `meta.signflip_channel` | after flipping sign on target channel, invariant/fingerprint **holds** | polarity assumption broken → graded invariant fails | decide on one target e.g. `score_delta` or control effort; fingerprint may include sign-normalized form for conforming | emit values and `meta.signflip_channel`; conforming uses odd-symmetric metric |
| SATEXTEND | open_loop | `channels.<target>.values`, `meta.sat_lo`, `meta.sat_hi` | wider/narrower sat bounds still respect property | sat pushes into violation region | cassette optional; encode bounds in meta; decisions stay legal on conforming | fixture with clip bounds; violating clips into bad band |
| HISTSWAP | open_loop | `channels.history.values` or event prefix, `meta.hist_splice_at` | splice/swap history buffers; property holds | corrupt/misalign history → fail | cassette may hold pre-splice state; document splice index | twin traces with swapped delay-line / context window |
| TRAJSWAP | **crn_closed_loop** | `channels.trajectory.*`, `channels.closed_loop_residual.values` | swap trajectory/reference segments; CRN residual stays in bound | swap breaks closed-loop invariant | must tag coupling crn_closed_loop; cassette can be dual-play logs | two closed-loop rollouts; swap mid-horizon refs |
| VARSCALE | **crn_closed_loop** | `channels.variance_proxy.values` or noise scale `meta.var_scale`, mean-matched | scale variance within bound; conservative ordering holds | scale breaks bound/ordering | coupling crn_closed_loop; mean-matched twins | mean-matched variance twins; **not** AUROC |

## ZeroDay decision / SARIF fields (all ops that use fingerprints)
Required when grading decisions:
- `meta.decision_fingerprint` = `sha256:<hex>`
- `meta.rule_ids` = string[]
- `meta.sarif_result_count` = int
Optional: SARIF `partialFingerprints` — ignored unless listed in `meta.grade_sarif_keys`

Hard omit everywhere: exploit/PoC/payload fields, attack repro, AUROC/model scores.

## AOMB note
Implement substantive fixture twins from this table even before harness tightens; harness `adapters/aomb.py` will validate 0.2 + axis presence. Ping draft PR URL when open.
