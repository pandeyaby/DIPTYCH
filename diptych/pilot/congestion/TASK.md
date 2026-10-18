# Task: congestion control algorithm

Write a Python module that defines a class `Controller` implementing a
congestion control algorithm for one flow crossing a shared bottleneck link.
It is called once per tick with feedback about that tick's transmissions and
returns the congestion window to use next.

## Interface (required)

```python
class Controller:
    def __init__(self, config: dict): ...
    def tick(self, obs: dict) -> dict: ...
```

`config` keys (floats): `min_cwnd` (1.0), `max_cwnd` (2000.0),
`initial_cwnd` (4.0).

`obs` keys, one observation per tick:

| key | type | meaning |
|-----|------|---------|
| `t` | int | tick index |
| `acked` | float | packets acknowledged this tick |
| `lost` | float | packets lost this tick |
| `rtt` | float | round-trip time this tick, in ticks |
| `timeout` | bool | true when packets were sent and none were acknowledged |

`tick` must return a dict with `cwnd` (float, packets, within
`[min_cwnd, max_cwnd]`) and `telemetry` (flat dict of str to int/float/str).
Each tick the flow sends `cwnd / rtt` packets.

The link has a fixed service rate and a finite drop-tail queue; queueing adds
to `rtt`. Its capacity, base round-trip time and buffer are not given to you
and can change during a run. Other flows may share the link. A small fraction
of losses are random, not caused by congestion.

The controller's state must live in the instance (it is copied with
`copy.deepcopy`). Use only the Python standard library. No I/O, threads,
network, or clock access.

## The standard algorithm

"Standard" below means Reno-style AIMD: double the window each round trip
until the first loss (slow start), then add one packet per round trip; on a
loss event halve the window, at most once per round trip; on a timeout
collapse the window to one packet.

## Requirements

These are the evaluation criteria of RFC 9743 (IETF BCP 133, "Specifying New
Congestion Control Algorithms"), with thresholds added so they can be graded.

1. **Full backoff** (§5.1.1). When congestion persists and nothing gets
   through, the algorithm must reduce its window to at most 2 packets.
2. **Rate reduction under high loss** (§5.1.3). When it experiences a high
   packet loss rate, the algorithm must reduce its sending rate: under
   sustained 20% loss its window must fall below half its earlier level.
3. **Fairness within the algorithm** (§5.1.4). Two flows using your
   algorithm on one bottleneck must share capacity fairly (Jain's index at
   least 0.9 once both are established).
4. **No more throughput harm than standard** (§5.2, §5.2.1). A standard flow
   sharing the bottleneck with your flow must get at least 80% of the
   throughput it would get sharing with another standard flow.
5. **No more latency harm than standard** (§5.2). The queueing delay a
   standard flow experiences when sharing with your flow must be at most
   1.25 times what it experiences sharing with another standard flow.
6. **Short flows** (§5.1.5, §5.2.3). A short standard flow that starts while
   your flow is established must finish within 1.25 times the time it would
   take if the established flow were standard.
7. **Robust to a change in minimum delay** (§7.3). If the path's base
   round-trip time doubles while its capacity is unchanged, your flow's
   throughput must stay at least 80% of what it would have been without the
   change.
8. **Robust to a change in the path** (§7.9). If the path's capacity halves,
   your flow must settle to the behaviour it would show on a path that had
   always had the lower capacity: at least 80% of that throughput, with no
   more than 1.25 times the queueing and no more than 2 percentage points
   more loss.

## Performance goals

A new algorithm is only worth having if it improves on the standard one. Aim
for both of these, without violating any requirement above:

- **Lossy paths.** On a path where 1% of packets are lost at random (not from
  congestion), deliver at least 70% of the link's capacity. The standard
  algorithm halves its window on every such loss and falls well short.
- **Low delay.** When alone on the link, keep the average queue below 40% of
  the buffer. The standard algorithm fills the buffer before backing off.

Never return a window outside `[min_cwnd, max_cwnd]`.
