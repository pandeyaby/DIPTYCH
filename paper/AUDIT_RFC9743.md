# Audit of RFC 9743 (paper §IV, Table II)

**Document.** M. Duke and G. Fairhurst (eds.), *Specifying New Congestion
Control Algorithms*, RFC 9743, BCP 133, IETF, March 2025.
https://www.rfc-editor.org/rfc/rfc9743 · doi:10.17487/RFC9743

**Scope.** Sections 5 (Evaluation Criteria) and 7 (Special Cases). We include
every criterion that constrains, or asks for an evaluation of, the
algorithm's *behavior*. We exclude documentation duties (§5.3.1 explain
deviations from earlier principles; §5.3.2 discuss incremental deployment;
§6.2 consider tunnels; §7.11 indicate which algorithms coexist) and
descriptions of environments in which the §5 criteria are to be evaluated
(§6.1, §6.3, §6.4, §7.4, §7.5, §7.7).

**Rule.** A criterion is *paired* iff its text defines the quantity as an
effect, impact, or harm relative to a reference situation, or as robustness
to a change. Otherwise it is a property of one run: a *trace property* if it
is a yes/no statement about the run, a *quantitative trace* property if it is
a measured quantity of the run.

| # | Section | Criterion (paraphrased) | Class | Contrast |
|---|---------|-------------------------|-------|----------|
| 1 | 5.1.1 | Full backoff or stop under persistent congestion | trace property | |
| 2 | 5.1.2 | Avoid maintaining excessive queues | quantitative trace | |
| 3 | 5.1.3 | Avoid causing high loss; reduce rate under high loss | quantitative trace | |
| 4 | 5.1.4 | How capacity is shared among flows of the same algorithm | quantitative trace | |
| 5 | 5.1.5 | How short-lived flows affect long-lived flows, and vice versa | **paired** | with vs. without the other flows |
| 6 | 5.2 | More harm than standard algorithms to flows sharing a bottleneck | **paired** | proposed vs. standard algorithm |
| 7 | 5.2 | No starvation; backoff when all feedback is lost | trace property | |
| 8 | 5.2.1 | Negative impact on flows using standard congestion control | **paired** | proposed vs. standard competitor |
| 9 | 5.2.2 | Coexistence with real-time congestion control | quantitative trace | |
| 10 | 5.2.3 | Effect on short and long flows using other algorithms | **paired** | with vs. without the proposed flows |
| 11 | 7.1 | Reaction to ECN congestion marks conforms to the codepoint | trace property | |
| 12 | 7.2 | Operates within the envelope of network circuit breakers | trace property | |
| 13 | 7.3 | Robust to a significant change in minimum delay | **paired** | with vs. without the delay change |
| 14 | 7.6 | Performance with misbehaving nodes or attackers | quantitative trace | |
| 15 | 7.8 | Performance under transient events | quantitative trace | |
| 16 | 7.9 | Impact of path changes; robust to them | **paired** | with vs. without the path change |
| 17 | 7.10 | Failover: harm to performance from a path change | **paired** | with vs. without failover |
| 18 | 7.10 | Concurrent multipath: harm to other flows at a shared bottleneck | **paired** | multipath vs. single-path flow |

**Totals.** 18 behavioral criteria: 8 paired, 6 quantitative trace, 4 trace
property.

**Borderline calls.**
- §5.2.2 (row 9): "coexistence" could be read as impact on real-time flows
  (paired). We classify it single-run because the RFC describes real-time
  flows as having their own throughput needs and latency bounds, which one
  run can be checked against.
- §7.6 and §7.8 (rows 14, 15): "how it performs" under misbehaving nodes or
  transient events names no reference situation, so the rule gives single-run.
  A reader who takes "performs" as "relative to the undisturbed case" would
  count them paired (10 of 18).
- §7.10 failover (row 17) also asks to show no starvation, a trace property;
  we classify the row by its harm clause.

Moving the borderline rows gives a range of 8 to 11 paired criteria out of 18.

**Status.** The classification is the authors' reading of one document that
guides human evaluation; it is not a benchmark specification. Both authors
should re-check each row against the RFC text before submission.
