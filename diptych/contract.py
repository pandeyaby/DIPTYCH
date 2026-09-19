"""Validate diptych_schema 0.2 envelopes (CONTRACT.md)."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any
from diptych import COUPLINGS, CONTROL_ROLES, CRN_REQUIRED, OPERATORS, SCHEMA, SOURCES, VERDICTS

HARD = (
    "diptych_schema", "source", "operator", "coupling",
    "probe_id", "control_role", "traces", "expected_verdict",
)

class ContractError(ValueError):
    pass

def validate_envelope(doc: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(doc, dict):
        raise ContractError("envelope must be object")
    for k in HARD:
        if k not in doc:
            raise ContractError(f"missing hard key: {k}")
    if doc["diptych_schema"] != SCHEMA:
        raise ContractError(f"diptych_schema must be {SCHEMA!r}")
    if doc["source"] not in SOURCES:
        raise ContractError(f"source must be one of {sorted(SOURCES)}")
    op = str(doc["operator"]).upper()
    if op not in OPERATORS:
        raise ContractError(f"unknown operator {op}")
    doc = {**doc, "operator": op}
    if doc["coupling"] not in COUPLINGS:
        raise ContractError("bad coupling")
    if op in CRN_REQUIRED and doc["coupling"] != "crn_closed_loop":
        raise ContractError(f"{op} requires crn_closed_loop")
    if doc["control_role"] not in CONTROL_ROLES:
        raise ContractError("bad control_role")
    if doc["expected_verdict"] not in VERDICTS:
        raise ContractError("bad expected_verdict")
    if doc["control_role"] == "conforming" and doc["expected_verdict"] != "pass":
        raise ContractError("conforming must expected_verdict=pass")
    if doc["control_role"] == "violating" and doc["expected_verdict"] != "fail":
        raise ContractError("violating must expected_verdict=fail")
    traces = doc["traces"]
    if not isinstance(traces, list) or len(traces) < 2:
        raise ContractError("traces length >= 2 required")
    for i, tr in enumerate(traces):
        if not isinstance(tr, dict):
            raise ContractError(f"traces[{i}] must be object")
        for req in ("trace_id", "channels", "meta"):
            if req not in tr:
                raise ContractError(f"traces[{i}] missing {req}")
        tr.setdefault("events", [])
        if "seed" not in tr["meta"]:
            raise ContractError(f"traces[{i}].meta.seed required")
        if op in CRN_REQUIRED and "crn_stream_id" not in tr["meta"]:
            raise ContractError(f"{op} traces[{i}].meta.crn_stream_id required")
    if op in CRN_REQUIRED:
        flagged = bool(doc.get("crn_closed_loop"))
        for tr in traces:
            if tr["meta"].get("crn_closed_loop") is True:
                flagged = True
        if not flagged:
            raise ContractError(f"{op} requires crn_closed_loop=true")
    blob = json.dumps(doc)
    for banned in ('"auroc"', '"AUROC"', '"lab_auroc"', '"model_grade"'):
        if banned in blob:
            raise ContractError(f"forbidden score field {banned}")
    return doc

def load_probe(path: str | Path) -> dict[str, Any]:
    return validate_envelope(json.loads(Path(path).read_text(encoding="utf-8")))
