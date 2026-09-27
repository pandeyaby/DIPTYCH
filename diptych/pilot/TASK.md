# Task: adaptive concurrency controller

Write a Python module that defines a class `Controller`. It regulates the
concurrency limit of a service so that observed latency tracks a latency
setpoint. It is called once per tick in a closed loop: your limit determines
how much traffic is admitted, which determines the latency you observe next.

## Interface (required)

```python
class Controller:
    def __init__(self, config: dict): ...
    def tick(self, obs: dict) -> dict: ...
    def snapshot(self) -> str: ...
    @classmethod
    def restore(cls, blob: str) -> "Controller": ...
```

`config` keys (all floats): `min_limit` (1.0), `max_limit` (200.0),
`initial_limit` (50.0).

`obs` keys, one observation per tick:

| key | type | meaning |
|-----|------|---------|
| `t` | int | tick index, starting at 0 |
| `latency_ms` | float | latency observed this tick |
| `setpoint_ms` | float | target latency (may change during a run) |
| `arrival_rps` | float | offered load this tick |
| `admitted_rps` | float | load admitted under your previous limit |

`tick` must return a dict with:

- `limit` (float): the new concurrency limit, within `[min_limit, max_limit]`
- `telemetry` (dict): flat mapping of string keys to int/float/str values
  describing your internal state

`snapshot` returns a string; `Controller.restore(blob)` must return a
controller that continues exactly as the original would have.

Use only the Python standard library. No I/O, threads, network, or clock
access.

## Requirements

1. **Asymmetric response.** The controller's downward correction to a
   deviation from setpoint must strictly exceed twice its upward correction to
   a deviation of equal magnitude and opposite sign.
2. **Trend recency.** Recent observations must carry more weight than older
   ones: after a run of improving latency the limit must end higher than after
   the same observations in worsening order.
3. **Anti-windup.** After sustained saturation at `max_limit`, the first
   response to a latency excursion must be bounded by the immediately observed
   error and not by how long the controller was saturated.
4. **Volatility suppression.** Given two workloads with equal mean and
   different variance, the controller must converge to a strictly more
   conservative (lower) limit on the higher-variance workload.
5. **Setpoint-change quiet.** After `setpoint_ms` changes, the controller's
   corrections must depend only on observations made after the change, not on
   the history before it.
6. **Determinism.** The same configuration and observation sequence must
   yield a bit-identical decision sequence (limits and telemetry) across fresh
   interpreter processes, regardless of hash seed or global random state.
7. **State round-trip stability.** Restoring from `snapshot()` at any tick
   must yield bit-identical subsequent decisions.
8. **Telemetry schema stability.** Every `telemetry` dict, in every tick of
   every run, must have exactly the same set of keys.

Also: keep latency near the setpoint without starving the service, and never
return a limit outside `[min_limit, max_limit]`.
