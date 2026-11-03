# RQ3 pre-registration: do paired verdicts predict held-out harm?

Registered by committing this file, `diptych/pilot/heldout.py` and
`diptych/pilot/rq3.py` before any held-out outcome was computed for a
generated controller. The commit hash and date are the registration.

## Question

The pilot shows that paired grading flags requirement violations that
single-trace grading misses. RQ3 asks whether those violations *matter*:
does a controller flagged by a paired probe do worse in deployment
conditions that no probe, baseline, or calibration step used?

## Units and data

- Controllers: every generated controller in `artifacts/manifest.json` for
  Task A (concurrency), Task B (congestion), Task C (autoscale). Controls are
  excluded from the analysis.
- Requirements: the behavioral requirements with a paired probe. Task A:
  asymmetric response (SIGNFLIP), trend recency (TRAJSWAP), volatility
  suppression (VARSCALE), anti-windup (SATEXTEND), setpoint-change quiet
  (HISTSWAP). Task B: harm-throughput, harm-latency, short-flows,
  delay-change, path-change. Task C: missing-metrics, not-ready,
  window-history, ratio-only, flapping. Determinism, round-trip, and schema
  requirements are excluded: their held-out analogue is the same property on
  new inputs, not a harm.
- Verdicts: paired, basic single-trace, and strong single-trace, as written
  by `python -m diptych.pilot.study [--task T]` (seeds 0-2).
- Cell: (controller, requirement). Cells with an `error` or `inconclusive`
  verdict under any grader, or a controller whose held-out run errors, are
  excluded and counted.

## Held-out scenarios and outcomes

Defined in `diptych/pilot/heldout.py`. Seeds 100, 101, 102 (probes use 0-2);
every outcome is the mean over the three seeds; higher is worse.

| Task | Requirement | Held-out scenario | Outcome H |
|------|-------------|-------------------|-----------|
| A | asymmetric response | three load surges above capacity | fraction of ticks overloaded (latency > 1.5x setpoint) or starving (latency < 0.5x setpoint while rejecting) |
| A | trend recency | slow creep of demand up then down | same, ticks 50-300 |
| A | volatility suppression | variance regime 4 -> 22 -> 4 rps | same, high-variance regime |
| A | anti-windup | long idle (limit saturates), then a rush | same, first 30 ticks of the rush |
| A | setpoint-change quiet | three setpoint moves | same, after the first move |
| B | harm-throughput | candidate joins two standard flows on a 15 pkt/tick, RTT 3 path | standard flows' shortfall from fair share |
| B | harm-latency | same run | mean standing queue / buffer |
| B | short-flows | eight 100-packet standard flows arrive beside the candidate | mean completion time |
| B | delay-change | base RTT 2 -> 4 -> 3 -> 6 | lost utilization |
| B | path-change | capacity 10 -> 6 -> 12 -> 4 | loss + queue + utilization shortfall, settled windows |
| C | missing-metrics | 30% of ready pods' metrics missing, cyclic demand | mean relative mis-provisioning |
| C | not-ready | ramp with 6-tick pod startup | replica-ticks above 1.2x need |
| C | window-history | three short spikes, long quiet periods | excess pods long after each spike |
| C | ratio-only | target 0.75 (probes use 0.6 and 0.9) | mean relative tracking error |
| C | flapping | steady load, noise 0.15, 5% missing | scaling actions per 100 ticks |

## Hypotheses and tests

Pooled AUC: within each (task, requirement) stratum, the probability that a
flagged cell has worse H than an unflagged cell (ties count half), pooled
over strata weighted by flagged x unflagged pairs.

- **H1 (primary, incremental validity).** Among cells the basic single-trace
  check passes, paired-fail cells have worse H: pooled AUC > 0.5, one-sided
  stratified permutation test (10,000 permutations within strata), alpha
  0.05. Tested per task and pooled over tasks; the pooled test is primary.
- **H2.** Pooled AUC of the paired verdict, the basic verdict, and the strong
  verdict over all cells; 95% bootstrap CI (2,000 resamples of controllers)
  of paired minus basic and paired minus strong.
- **H3.** Per controller: Spearman correlation between the number of paired
  failures and the composite held-out rank (mean within-requirement
  percentile of H), compared with the same for basic and strong failures.

All results are reported whatever their direction. A null or negative H1
will be reported as such in the paper.

## Decisions made before registration (controls only)

The held-out code was run on the executable controls (never on generated
controllers) to check that it executes. One change followed: Task A's band
was widened from 0.75-1.25x setpoint to 0.5-1.5x, because on this plant the
narrow band spans only about 68-83 admitted rps. With either band the
conforming control scores poorly on the surge and idle-then-rush scenarios
(it cuts its limit to the floor under overload and recovers slowly); we did
not change the scenarios or the control to alter that.

## Known limitations

Outcomes are our choice of harm for each requirement, and simulators are
ours. A violation can be harmless in one deployment and harmful in another;
H1 tests whether flags predict harm on average in these deployments, not in
every deployment.
