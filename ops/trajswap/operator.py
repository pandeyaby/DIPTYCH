"""Pure transform for TRAJSWAP (trace_a, trace_b, params) -> probe_pair."""
from __future__ import annotations
from typing import Any

OPERATOR = "TRAJSWAP"
COUPLING = "crn_closed_loop"


def apply(trace_a: dict[str, Any], trace_b: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    """Return a probe envelope skeleton; grading lives in diptych.grade."""
    return {
        "diptych_schema": "0.2",
        "source": "diptych_core",
        "operator": OPERATOR,
        "coupling": params.get("coupling", COUPLING),
        "probe_id": params.get("probe_id", f"diptych_core.{OPERATOR.lower()}.pair"),
        "control_role": params.get("control_role", "conforming"),
        "expected_verdict": params.get("expected_verdict", "pass"),
        "traces": [trace_a, trace_b],
    }
