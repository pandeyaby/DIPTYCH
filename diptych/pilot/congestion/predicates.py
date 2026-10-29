"""Requirement predicates for the congestion-control task.

Requirements paraphrase RFC 9743 criteria; the thresholds are ours (the RFC
gives none). Each predicate reads one live envelope and returns
``(verdict, reason, evidence)``.
"""

from __future__ import annotations

from typing import Any

from diptych.live import predicate, values

TASK = "congestion"

SINGLE_RUN: tuple[str, ...] = ("full-backoff", "loss-reduction", "intra-fairness")
PAIRED: tuple[str, ...] = ("harm-throughput", "harm-latency", "short-flows", "delay-change", "path-change")

# RFC 9743 section each requirement paraphrases.
RFC_SECTION = {
    "full-backoff": "5.1.1", "loss-reduction": "5.1.3", "intra-fairness": "5.1.4",
    "harm-throughput": "5.2, 5.2.1", "harm-latency": "5.2", "short-flows": "5.1.5, 5.2.3",
    "delay-change": "7.3", "path-change": "7.9",
}

HARM_THROUGHPUT = 0.8    # victim keeps >= 80% of its throughput vs. a standard competitor
HARM_LATENCY = 1.25      # queue <= 1.25x the queue with a standard competitor
SHORT_FLOW = 1.25        # short flow completes within 1.25x
ROBUST_THROUGHPUT = 0.8
LOSS_MARGIN = 0.02

Result = tuple[str, str, dict[str, Any]]


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


@predicate(TASK, "harm-throughput")
def harm_throughput(doc: dict[str, Any]) -> Result:
    """Twins: the victim's throughput beside the candidate (a) / a standard flow (b)."""
    a, b = _mean(values(doc, 0, "victim_throughput")), _mean(values(doc, 1, "victim_throughput"))
    if b <= 0:
        return "inconclusive", "victim starved even beside a standard flow", {}
    ev = {"victim_vs_candidate": a, "victim_vs_standard": b, "ratio": a / b}
    return ("pass" if a >= HARM_THROUGHPUT * b else "fail"), str(ev), ev


@predicate(TASK, "harm-latency")
def harm_latency(doc: dict[str, Any]) -> Result:
    """Twins: bottleneck queue beside the candidate (a) / a standard flow (b)."""
    a, b = _mean(values(doc, 0, "queue")), _mean(values(doc, 1, "queue"))
    ev = {"queue_vs_candidate": a, "queue_vs_standard": b}
    return ("pass" if a <= HARM_LATENCY * b + 0.5 else "fail"), str(ev), ev


@predicate(TASK, "short-flows")
def short_flows(doc: dict[str, Any]) -> Result:
    """Twins: a short flow beside the candidate (a) / beside a standard flow (b)."""
    ta = doc["traces"][0]["meta"].get("completion_ticks")
    tb = doc["traces"][1]["meta"].get("completion_ticks")
    ev = {"completion_beside_candidate": ta, "completion_beside_standard": tb}
    if tb is None:
        return "inconclusive", "short flow did not finish even beside a standard flow", ev
    if ta is None:
        return "fail", "short flow did not finish beside the candidate", ev
    return ("pass" if ta <= SHORT_FLOW * tb + 2 else "fail"), str(ev), ev


@predicate(TASK, "delay-change")
def delay_change(doc: dict[str, Any]) -> Result:
    """Twins: throughput after the fork with base RTT unchanged (a) / doubled (b)."""
    a, b = values(doc, 0, "throughput"), values(doc, 1, "throughput")
    half = len(a) // 2
    ma, mb = _mean(a[half:]), _mean(b[half:])
    if ma <= 0:
        return "inconclusive", "no throughput on the unchanged path", {}
    ev = {"throughput_unchanged": ma, "throughput_delay_doubled": mb, "ratio": mb / ma}
    return ("pass" if mb >= ROBUST_THROUGHPUT * ma else "fail"), str(ev), ev


@predicate(TASK, "path-change")
def path_change(doc: dict[str, Any]) -> Result:
    """Twins: settled behaviour after capacity halves (a) / on an always-low path (b)."""
    thr = [_mean(values(doc, i, "throughput")) for i in (0, 1)]
    queue = [_mean(values(doc, i, "queue")) for i in (0, 1)]
    loss = []
    for i in (0, 1):
        lost, got = sum(values(doc, i, "lost")), sum(values(doc, i, "throughput"))
        loss.append(lost / (lost + got) if lost + got > 0 else 0.0)
    if thr[1] <= 0:
        return "inconclusive", "no throughput on the always-low path", {}
    ev = {"throughput": thr, "queue": queue, "loss_rate": loss}
    ok = (thr[0] >= ROBUST_THROUGHPUT * thr[1]
          and queue[0] <= HARM_LATENCY * queue[1] + 0.5
          and loss[0] <= loss[1] + LOSS_MARGIN)
    return ("pass" if ok else "fail"), str(ev), ev
