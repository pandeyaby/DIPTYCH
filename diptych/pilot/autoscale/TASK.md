# Task: horizontal pod autoscaler

Write a Python module that defines a class `Controller` implementing a
horizontal autoscaler for one service. It is called once per sync period
(one tick, 15 seconds) with the state of the service's pods and returns the
number of replicas the service should run.

## Interface (required)

```python
class Controller:
    def __init__(self, config: dict): ...
    def tick(self, obs: dict) -> dict: ...
```

`config` keys: `min_replicas` (1), `max_replicas` (60), `initial_replicas`
(10), `target_utilization` (0.6), `tolerance` (0.1), `stabilization_ticks`
(20, i.e. 5 minutes).

`obs` keys, one observation per tick:

| key | type | meaning |
|-----|------|---------|
| `t` | int | tick index |
| `replicas` | int | pods currently running (ready or starting) |
| `pods` | list of dict | one entry per pod: `ready` (bool), `utilization` (float or None) |

`utilization` is the pod's CPU use as a fraction of its request. It is None
for a pod that is not yet ready, and occasionally None for a ready pod whose
metric is missing. Measurements are noisy.

`tick` must return a dict with `replicas` (int, within
`[min_replicas, max_replicas]`) and `telemetry` (flat dict of str to
int/float/str). The new count takes effect at once: new pods start and
become ready a few ticks later; surplus pods are removed. Load is shared
evenly by the ready pods.

The controller's state must live in the instance (it is copied with
`copy.deepcopy`). Use only the Python standard library. No I/O, threads,
network, or clock access.

## Requirements

These follow the Kubernetes documentation for the Horizontal Pod Autoscaler
("Horizontal Pod Autoscaling", kubernetes.io), with thresholds added where
the documentation gives none. "The base recommendation" is the documented
formula: `ceil(k * u / target_utilization)`, where `k` is the number of
ready pods with a metric and `u` their average utilization; when
`u / target_utilization` is within `tolerance` of 1 it is the current count.

1. **Tolerance** (Algorithm details; Tolerance). When the average
   utilization of the ready pods is within `tolerance` of the target ratio,
   do not change the replica count.
2. **Scale-up rate** (Default behavior). In one tick, add at most
   `max(4, current replicas)` pods.
3. **Downscale stabilization** (Algorithm details; Stabilization window).
   When scaling down, never go below the highest base recommendation of the
   last `stabilization_ticks` ticks, this one included.
4. **Missing metrics** (Algorithm details). Missing metrics must only damp a
   scaling decision: if some ready pods' metrics were withheld, the decision
   must lie between the current replica count and the decision you would
   make with those metrics present.
5. **Pods that are starting** (Algorithm details). Pods that are not yet
   ready must not enlarge a scale-up: the decision must be no larger than
   the one you would make if those pods were ready and reporting 0%
   utilization (or the current count, if that is larger).
6. **Bounded memory** (Stabilization window). Only the recent past may
   matter: two observation histories that are identical over their last
   `stabilization_ticks + 10` ticks must lead to the same decision.
7. **Ratio only** (Algorithm details). Decisions may depend on utilization
   only through its ratio to `target_utilization`: multiplying the target
   and every pod's utilization by the same factor must not change any
   decision.
8. **No flapping** (Stability of workload scale). Under steady load, noisier
   utilization measurements must not make the replica count oscillate: with
   per-pod measurement noise six times larger, the count may reverse
   direction (up then down, or down then up) at most 2 more times over 100
   minutes.

## Performance goals

An autoscaler is only worth having if it keeps the service up without
wasting pods. Aim for both of these, without violating any requirement
above:

- **Flash crowds.** When demand quadruples suddenly and stays high for 7.5
  minutes, drop at most 3% of the requests in that period.
- **Cost.** Over a daily-style demand cycle, run on average at most 1.3
  times the minimum number of pods needed to stay at the target.

Never return a count outside `[min_replicas, max_replicas]`.
