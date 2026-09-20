"""Public importable DIPTYCH harness API (diptych_schema 0.2).

Stranger / adapter usage::

    from diptych import (
        parse_probe,
        grade_operator,
        run_gate_axis_mutate,
        ProbeEnvelope,
        OPERATORS,
    )

    env = parse_probe("diptych-probes/RESEED/conforming/probe.json")
    result = grade_operator(env)
    ok, evidence = run_gate_axis_mutate("RESEED", env)
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from diptych.contract import (
    ContractError,
    load_probe,
    parse_envelope,
    reject_thin_envelope,
    validate_envelope,
)
from diptych.gates import Failure, Report, gate_axis_mutate, run_gates, write_matrix
from diptych.grade import GradeResult, grade_document
from diptych.mutate_axis import (
    AXIS_SPECS,
    SemanticWitness,
    mutate_axis,
    mutate_wrong_axis,
)
from diptych.schema import (
    AXIS_REQUIREMENTS,
    CONTROL_ROLE_ENUM,
    COUPLING_ENUM,
    OPERATOR_ENUM,
    SOURCE_ENUM,
    VERDICT_ENUM,
    ControlRole,
    Coupling,
    Horizon,
    OperatorName,
    ProbeEnvelope,
    ProbeEnvelopeDict,
    SourceName,
    Trace,
    Verdict,
)

# Re-export aliases preferred by strangers / adapters.
parse_probe = parse_envelope
validate_probe = validate_envelope
load_probe_pair = load_probe


def grade_operator(
    doc: dict[str, Any] | ProbeEnvelope | str | Path,
) -> GradeResult:
    """Grade one probe-pair envelope (dict, ProbeEnvelope, or path)."""
    if isinstance(doc, ProbeEnvelope):
        payload = doc.to_dict()
    elif isinstance(doc, (str, Path)):
        payload = load_probe(doc)
    else:
        payload = doc
    return grade_document(payload)


def run_gate_axis_mutate(
    operator: str,
    conforming: dict[str, Any] | ProbeEnvelope | str | Path,
) -> tuple[bool, dict[str, Any]]:
    """Run gate_axis_mutate; return (power_ok, evidence).

    ``power_ok`` is True only when semantic witness + pass→fail flip succeed.
    Failures are embedded in evidence under ``failures`` when not ok.
    """
    if isinstance(conforming, ProbeEnvelope):
        conf = conforming.to_dict()
    elif isinstance(conforming, (str, Path)):
        conf = load_probe(conforming)
    else:
        conf = conforming
    failures, evidence = gate_axis_mutate(operator, conf)
    power_ok = evidence.get("power_ok") is True and not failures
    out = dict(evidence)
    if failures:
        out["failures"] = [f.__dict__ for f in failures]
    return power_ok, out


def envelope_round_trip(doc: dict[str, Any]) -> dict[str, Any]:
    """Validate → ProbeEnvelope → dict; used by contract tests."""
    env = parse_envelope(doc)
    back = env.to_dict()
    validate_envelope(back)
    return back


__all__ = [
    "AXIS_REQUIREMENTS",
    "AXIS_SPECS",
    "CONTROL_ROLE_ENUM",
    "COUPLING_ENUM",
    "ContractError",
    "ControlRole",
    "Coupling",
    "Failure",
    "GradeResult",
    "Horizon",
    "OPERATOR_ENUM",
    "OperatorName",
    "ProbeEnvelope",
    "ProbeEnvelopeDict",
    "Report",
    "SOURCE_ENUM",
    "SemanticWitness",
    "SourceName",
    "Trace",
    "VERDICT_ENUM",
    "Verdict",
    "envelope_round_trip",
    "gate_axis_mutate",
    "grade_document",
    "grade_operator",
    "load_probe",
    "load_probe_pair",
    "mutate_axis",
    "mutate_wrong_axis",
    "parse_envelope",
    "parse_probe",
    "reject_thin_envelope",
    "run_gate_axis_mutate",
    "run_gates",
    "validate_envelope",
    "validate_probe",
    "write_matrix",
]
