"""Live envelopes: paired executions recorded from an artifact under test.

Schema 0.2 has two envelope kinds that share one pipeline:

* **control envelopes** (``source`` in ``diptych.SOURCES``) carry a control
  role and an expected verdict; they are the hand-written fixtures and the
  adapter emissions graded in ``diptych.grade``.
* **live envelopes** (``source == "live"``) carry a pair of traces recorded by
  a probe from a running artifact, plus the ``task`` and ``requirement`` the
  pair is evidence for. They have no expected verdict.

Both kinds go through the same steps::

    validate -> comparability (shared-prefix integrity, CRN stream identity)
             -> predicate over the recorded pair -> pass | fail | inconclusive

Probes only *record*; predicates only *read the envelope*. A saved live
envelope can therefore be re-graded offline with ``diptych-grade`` and must
give the verdict the study reported.

Predicates are registered per ``(task, requirement)`` with :func:`predicate`.
A predicate returns ``(verdict, reason, evidence)`` and may itself return
``inconclusive`` for requirement-specific comparability (for example, a branch
that hit an actuator bound).
"""

from __future__ import annotations

import json
from typing import Any, Callable

from diptych import COUPLINGS, OPERATORS, SCHEMA, VERDICTS
from diptych.contract import FORBIDDEN_SCORE_FIELDS, ContractError

LIVE_SOURCE = "live"
LIVE_ROLE = "artifact"
# Perturbation families used by live probes beyond the eight control
# operators: REFSWAP substitutes a reference artifact for the one under test
# (everything else held fixed); PATHSTEP steps one exogenous environment
# parameter on one arm after the fork.
LIVE_OPERATORS: tuple[str, ...] = OPERATORS + ("REFSWAP", "PATHSTEP")

Predicate = Callable[[dict[str, Any]], tuple[str, str, dict[str, Any]]]
PREDICATES: dict[tuple[str, str], Predicate] = {}


def predicate(task: str, requirement: str) -> Callable[[Predicate], Predicate]:
    """Register the predicate that grades ``requirement`` of ``task``."""

    def deco(fn: Predicate) -> Predicate:
        PREDICATES[(task, requirement)] = fn
        return fn

    return deco


def twin(trace_id: str, channels: dict[str, list], seed: int, **meta: Any) -> dict[str, Any]:
    """One recorded arm: named value series plus its metadata."""
    return {
        "trace_id": trace_id,
        "events": [],
        "channels": {name: ({"keys": list(v)} if name == "schema" else {"values": list(v)})
                     for name, v in channels.items()},
        "meta": {"seed": seed, **meta},
    }


def live_envelope(
    *,
    task: str,
    requirement: str,
    operator: str,
    coupling: str,
    artifact: str,
    probe_id: str,
    twins: list[dict[str, Any]],
    **meta: Any,
) -> dict[str, Any]:
    """Build a live envelope from recorded twins."""
    lengths = [len(ch.get("values", ch.get("keys", [])))
               for tr in twins for ch in tr["channels"].values()]
    return {
        "diptych_schema": SCHEMA,
        "source": LIVE_SOURCE,
        "task": task,
        "requirement": requirement,
        "operator": operator,
        "coupling": coupling,
        "horizon": {"unit": "steps", "length": max(lengths, default=0)},
        "probe_id": probe_id,
        "artifact": artifact,
        "traces": twins,
        "meta": dict(meta),
    }


def is_live(doc: Any) -> bool:
    return isinstance(doc, dict) and doc.get("source") == LIVE_SOURCE


def validate_live_envelope(doc: dict[str, Any]) -> dict[str, Any]:
    """Structural contract for live envelopes (mirrors ``validate_envelope``)."""
    if not isinstance(doc, dict):
        raise ContractError("envelope must be object")
    for k in ("diptych_schema", "source", "task", "requirement", "operator", "coupling",
              "horizon", "probe_id", "artifact", "traces"):
        if k not in doc:
            raise ContractError(f"missing hard key: {k}")
    if doc["diptych_schema"] != SCHEMA:
        raise ContractError(f"diptych_schema must be {SCHEMA!r}")
    if doc["source"] != LIVE_SOURCE:
        raise ContractError(f"source must be {LIVE_SOURCE!r}")
    op = str(doc["operator"]).upper()
    if op not in LIVE_OPERATORS:
        raise ContractError(f"unknown operator {op}")
    if doc["coupling"] not in COUPLINGS:
        raise ContractError("bad coupling")
    if "control_role" in doc or "expected_verdict" in doc:
        raise ContractError("live envelopes carry no control_role / expected_verdict")
    if (doc["task"], doc["requirement"]) not in PREDICATES:
        raise ContractError(f"no predicate registered for {doc['task']}/{doc['requirement']}")
    traces = doc["traces"]
    if not isinstance(traces, list) or len(traces) < 1:
        raise ContractError("traces must be a non-empty list")
    for i, tr in enumerate(traces):
        if not isinstance(tr, dict):
            raise ContractError(f"traces[{i}] must be object")
        for req in ("trace_id", "channels", "meta"):
            if req not in tr:
                raise ContractError(f"traces[{i}] missing {req}")
        if not isinstance(tr["channels"], dict) or not tr["channels"]:
            raise ContractError(f"traces[{i}].channels empty")
        if "seed" not in tr["meta"]:
            raise ContractError(f"traces[{i}].meta.seed required")
        if doc["coupling"] == "crn_closed_loop" and "crn_stream_id" not in tr["meta"]:
            raise ContractError(f"traces[{i}].meta.crn_stream_id required under crn_closed_loop")
    blob = json.dumps(doc)
    for banned in FORBIDDEN_SCORE_FIELDS:
        if banned in blob:
            raise ContractError(f"forbidden score field {banned}")
    return {**doc, "operator": op}


def _crn_mismatch(doc: dict[str, Any]) -> str | None:
    if doc["coupling"] != "crn_closed_loop":
        return None
    ids = {tr["meta"].get("crn_stream_id") for tr in doc["traces"]}
    return None if len(ids) == 1 else "traces incomparable under shared prefix rule: crn_stream_id mismatch"


def grade_live(doc: dict[str, Any]) -> Any:
    """Validate, check comparability, and apply the requirement predicate."""
    from diptych.grade._lib import GradeResult, comparability_reason

    doc = validate_live_envelope(doc)

    def result(verdict: str, reason: str, evidence: dict[str, Any]) -> Any:
        if verdict not in VERDICTS:
            raise ContractError(f"predicate returned unknown verdict {verdict!r}")
        return GradeResult(
            operator=doc["operator"], probe_id=doc["probe_id"], control_role=LIVE_ROLE,
            expected_verdict="", actual_verdict=verdict, reason=reason, evidence=evidence,
        )

    why = comparability_reason(doc) or _crn_mismatch(doc)
    if why:
        return result("inconclusive", why, {"comparability": why})
    verdict, reason, evidence = PREDICATES[(doc["task"], doc["requirement"])](doc)
    return result(verdict, reason, evidence)


def values(doc: dict[str, Any], i: int, channel: str) -> list[float]:
    """Recorded series ``channel`` of twin ``i``."""
    block = doc["traces"][i]["channels"].get(channel)
    if not isinstance(block, dict) or "values" not in block:
        raise ContractError(f"traces[{i}] missing channels.{channel}.values")
    return [float(x) for x in block["values"]]
