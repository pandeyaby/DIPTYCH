# ZeroDay → DIPTYCH (v0.2 full-8)

**Mandate:** All 8 operators as substantive product probes (conforming+violating each).

Fingerprints: `meta.decision_fingerprint` (`sha256:…`) + `meta.rule_ids` + `meta.sarif_result_count`.
Cassette: `serialize_restore` | `vcr_json` | `none` as applicable.
Paths: `diptych-probes/<OP>/<conforming|violating>/cassette.json|.bin`

TRAJSWAP/VARSCALE require `coupling: "crn_closed_loop"`.
Hard omit: exploit/PoC/payload/AUROC fields.

CI must fail on TODO/stub/hardcoded pass/missing violating twin.

## Deferred → green witness recipes (ZERODAY#41 uplift)

Canonical detail: [`WITNESSES_ZERODAY.md`](./WITNESSES_ZERODAY.md)

| Op | Valid witness (ZeroDay domain) | Not allowed |
|---|---|---|
| **SIGNFLIP** | Signed `channels.score_margin.values` = score(top1)−score(top2); odd-symmetric order invariant under sign flip | SARIF level note↔error rename; negating positive ranks only |
| **TRAJSWAP** | locate→verify CRN: `trajectory.*` + `closed_loop_residual` (e.g. 1−Jaccard); mid-horizon segment swap; residual ≤ ε vs > ε | open_loop; rankedFiles permute with empty residual |
| **VARSCALE** | `meta.var_scale` on exploration noise; `variance_proxy` at **mean-matched** finding count; stability floor | AUROC; scaling ranks/result counts as “variance” |

Optional `freeze_channels`: SIGNFLIP may share `["clock"]`; TRAJSWAP may freeze `["rng","clock"]` so residual is trajectory-attributable; VARSCALE must **not** freeze the scaled noise source.
