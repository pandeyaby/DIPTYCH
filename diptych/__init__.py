"""DIPTYCH core — diptych_schema 0.2 full-8 hyperproperty grading harness."""
from __future__ import annotations

OPERATORS: tuple[str, ...] = (
    "SIGNFLIP",
    "TRAJSWAP",
    "VARSCALE",
    "SATEXTEND",
    "HISTSWAP",
    "FREEZEDRY",
    "RESEED",
    "SCHEMAX",
)
CRN_REQUIRED = frozenset({"TRAJSWAP", "VARSCALE"})
SCHEMA = "0.2"
SOURCES = frozenset({"diptych_core", "zeroday", "aomb"})
CONTROL_ROLES = frozenset({"conforming", "violating"})
VERDICTS = frozenset({"pass", "fail", "inconclusive"})
COUPLINGS = frozenset({"open_loop", "crn_closed_loop"})
STUB_MARKERS = ("TODO", "NotImplemented", "not_implemented", "STUB_OPERATOR", "hardcoded_pass")

# Product adapter pins (not vendored into this repo)
ADAPTER_PINS = {
    "zeroday": "fb5b39daf88e37521aaee8526ae9d286cf74f341",  # ZeroDay@fb5b39da
    "aomb": "667e47538ae5b9c504187b7a73220d22aa8fb96f",  # AOMB@667e475
}

# Public harness API (typed envelopes + grade + gate_axis_mutate).
# Imported last so submodule `from diptych import OPERATORS` resolves.
from diptych.api import (  # noqa: E402
    ContractError,
    GradeResult,
    Horizon,
    ProbeEnvelope,
    ProbeEnvelopeDict,
    SemanticWitness,
    Trace,
    envelope_round_trip,
    grade_operator,
    parse_probe,
    reject_thin_envelope,
    run_gate_axis_mutate,
    validate_probe,
)

__all__ = [
    "ADAPTER_PINS",
    "CONTROL_ROLES",
    "COUPLINGS",
    "CRN_REQUIRED",
    "ContractError",
    "GradeResult",
    "Horizon",
    "OPERATORS",
    "ProbeEnvelope",
    "ProbeEnvelopeDict",
    "SCHEMA",
    "SOURCES",
    "STUB_MARKERS",
    "SemanticWitness",
    "Trace",
    "VERDICTS",
    "envelope_round_trip",
    "grade_operator",
    "parse_probe",
    "reject_thin_envelope",
    "run_gate_axis_mutate",
    "validate_probe",
]
