# DIPTYCH adapter one-pager (diptych_schema 0.2) — CI mirror source

**Enums (hard):**
- `diptych_schema`: `"0.2"` (0.1 rejected)
- `control_role`: `"conforming"` | `"violating"`
- `expected_verdict`: `"pass"` | `"fail"` | `"inconclusive"`
  - conforming → `pass`; violating → `fail`
  - `inconclusive` requires `meta.inconclusive_reason`; **not green**
- `coupling`: `"open_loop"` | `"crn_closed_loop"`
- `cassette.format`: `"serialize_restore"` | `"vcr_json"` | `"none"`
- `cassette.bytes_or_path`: `diptych-probes/<OP>/<conforming|violating>/cassette.json` (or `.bin`)

**Shared decision/SARIF fields** (when fingerprints are graded):
- `meta.decision_fingerprint`: `sha256:<hex>`
- `meta.rule_ids`: string[]
- `meta.sarif_result_count`: int
- Optional grade list: `meta.grade_sarif_keys` (default ignore other SARIF props)

**Reject:** TODO/stub/NotImplemented/hardcoded pass/empty traces/missing violating twin; cosmetic severity or score renames that do not change the hyperproperty axis; exploit/PoC/payload/AUROC fields.

**Coverage cell green iff:** (1) conforming→pass, (2) violating→fail, (3) power-on-axis: mutating only that operator’s axis flips or explains the verdict asymmetry (identical twins / deferred_without_semantic_witness ≠ green).

---

## FREEZEDRY — coupling `open_loop`
| | conforming | violating |
|---|---|---|
| freeze_channels | `["rng","clock"]` | `[]` or omit (leak allowed) |
| decision/SARIF | identical `decision_fingerprint` + `sarif_fingerprint.keys` | fingerprints **differ** |
| expected_verdict | `pass` | `fail` |
| cassette.format | `serialize_restore` | `serialize_restore` (same bytes, unfrozen exec) |
| power-on-axis | freeze mask on/off changes fingerprint equality | |

## RESEED — coupling `open_loop`
| | conforming | violating |
|---|---|---|
| freeze_channels | n/a (use `meta.seed`) | n/a |
| fields | `meta.seed` differs; `channels.stability.values`; `meta.epsilon` | same; stability **> ε** |
| expected_verdict | `pass` | `fail` |
| cassette.format | `none` or shared + different seed | same |
| power-on-axis | only seed changes; ε-bound holds vs breaks | |

## SCHEMAX — coupling `open_loop`
| | conforming | violating |
|---|---|---|
| freeze_channels | n/a | n/a |
| fields | `channels.schema.keys` or `meta.required_schema_keys` **equal** (order-invariant) | required key renamed/dropped |
| expected_verdict | `pass` | `fail` |
| cassette.format | `none` or dual fixture paths | dual paths |
| power-on-axis | required key-set equality vs inequality | |

## SIGNFLIP — coupling `open_loop`
| | conforming | violating |
|---|---|---|
| freeze_channels | optional shared freeze unrelated to polarity | same |
| fields | `meta.signflip_channel` + `channels.<target>.values`; invariant holds after sign flip | polarity/invariant breaks |
| decision/SARIF | may use sign-normalized fingerprint on conforming | unnormalized / broken |
| expected_verdict | `pass` | `fail` |
| cassette.format | `none` or `vcr_json` | same |
| power-on-axis | flipping named channel sign preserves vs breaks graded invariant | |

## SATEXTEND — coupling `open_loop`
| | conforming | violating |
|---|---|---|
| fields | `meta.sat_lo`, `meta.sat_hi` + clipped `channels.<target>.values`; property holds | saturation enters violation region |
| expected_verdict | `pass` | `fail` |
| cassette.format | `none` | `none` |
| power-on-axis | bound change alone causes pass→fail | |

## HISTSWAP — coupling `open_loop`
| | conforming | violating |
|---|---|---|
| fields | `channels.history.values` (or event prefix), `meta.hist_splice_at`; property holds after splice | misaligned/corrupt history → fail |
| expected_verdict | `pass` | `fail` |
| cassette.format | `serialize_restore` or `vcr_json` holding pre-splice state | same |
| power-on-axis | history splice integrity vs corruption | |

## TRAJSWAP — coupling **`crn_closed_loop`** (required)
| | conforming | violating |
|---|---|---|
| fields | `channels.trajectory.*`, `channels.closed_loop_residual.values`; residual in bound after segment swap | swap breaks closed-loop invariant |
| expected_verdict | `pass` | `fail` |
| cassette.format | dual-play logs `vcr_json` or paired paths | same |
| power-on-axis | trajectory segment identity vs swapped under CRN | |
| reject | open_loop tagging; cosmetic rename without residual contrast | |

## VARSCALE — coupling **`crn_closed_loop`** (required)
| | conforming | violating |
|---|---|---|
| fields | `meta.var_scale`, `channels.variance_proxy.values` (mean-matched); scale within bound / ordering holds | scale breaks bound/ordering |
| expected_verdict | `pass` | `fail` |
| cassette.format | `none` or paired closed-loop logs | same |
| power-on-axis | variance scale alone; **not** AUROC/score rename | |
| reject | publishing lab AUROC as hyperproperty grade | |

---

## Manifest paths (emitters)
```
diptych-probes/FREEZEDRY/conforming/...
diptych-probes/FREEZEDRY/violating/...
… (all 8 × both roles)
coverage/matrix.json  # Operator × {diptych_core,zeroday,aomb}
```
