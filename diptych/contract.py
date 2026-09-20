"""Validate diptych_schema 0.2 envelopes (CONTRACT.md / GATING.md)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from diptych import (
    COUPLINGS,
    CONTROL_ROLES,
    CRN_REQUIRED,
    OPERATORS,
    SCHEMA,
    SOURCES,
    STUB_MARKERS,
    VERDICTS,
)
from diptych.schema import AXIS_REQUIREMENTS

HARD = (
    "diptych_schema",
    "source",
    "operator",
    "coupling",
    "probe_id",
    "control_role",
    "traces",
    "expected_verdict",
)

FORBIDDEN_SCORE_FIELDS = ('"auroc"', '"AUROC"', '"lab_auroc"', '"model_grade"')


class ContractError(ValueError):
    pass


def _inconclusive_reason(doc: dict[str, Any]) -> str | None:
    """Return documented inconclusive reason if present (OPERATOR_TABLE / ONEPAGER)."""
    meta = doc.get("meta") if isinstance(doc.get("meta"), dict) else {}
    reason = meta.get("inconclusive_reason")
    if isinstance(reason, str) and reason.strip():
        return reason.strip()
    for tr in doc.get("traces") or []:
        if not isinstance(tr, dict):
            continue
        tmeta = tr.get("meta") if isinstance(tr.get("meta"), dict) else {}
        reason = tmeta.get("inconclusive_reason")
        if isinstance(reason, str) and reason.strip():
            return reason.strip()
    return None


def _axis_present(op: str, doc: dict[str, Any]) -> bool:
    """Operator-specific axis fields (GATING / OPERATOR_TABLE)."""
    traces = doc.get("traces") or []
    if not traces:
        return False
    t0 = traces[0]
    ch = t0.get("channels") or {}
    meta = t0.get("meta") or {}
    if op == "RESEED":
        return (
            "stability" in ch
            and isinstance((ch.get("stability") or {}).get("values"), list)
            and bool((ch["stability"]["values"]))
            and "epsilon" in meta
            and meta.get("seed") != (traces[1].get("meta") or {}).get("seed")
        )
    if op == "SCHEMAX":
        schema = ch.get("schema") or {}
        keys = schema.get("keys")
        req = meta.get("required_schema_keys")
        return (isinstance(keys, list) and len(keys) > 0) or (
            isinstance(req, list) and len(req) > 0
        )
    if op == "FREEZEDRY":
        return (
            "graded" in ch
            and isinstance((ch.get("graded") or {}).get("values"), list)
            and "freeze_channels" in meta
            and "decision_fingerprint" in meta
        )
    if op == "SIGNFLIP":
        target = meta.get("signflip_channel")
        if not target:
            return False
        block = ch.get(target) or {}
        return isinstance(block.get("values"), list) and len(block["values"]) > 0
    if op == "SATEXTEND":
        if "sat_lo" not in meta or "sat_hi" not in meta:
            return False
        target = meta.get("sat_channel", "actuator")
        block = ch.get(target) or {}
        return isinstance(block.get("values"), list) and len(block["values"]) > 0
    if op == "HISTSWAP":
        hist = ch.get("history") or {}
        return (
            isinstance(hist.get("values"), list)
            and len(hist["values"]) > 0
            and "hist_splice_at" in meta
        )
    if op == "TRAJSWAP":
        traj = ch.get("trajectory") or {}
        resid = ch.get("closed_loop_residual") or {}
        return (
            isinstance(traj.get("values"), list)
            and len(traj["values"]) > 0
            and isinstance(resid.get("values"), list)
            and len(resid["values"]) > 0
        )
    if op == "VARSCALE":
        proxy = ch.get("variance_proxy") or {}
        return (
            isinstance(proxy.get("values"), list)
            and len(proxy["values"]) > 0
            and "var_scale" in meta
        )
    return False


def reject_thin_envelope(doc: dict[str, Any]) -> None:
    """Reject stub/smoke/thin envelopes per GATING.md (raises ContractError)."""
    blob = json.dumps(doc)
    for marker in STUB_MARKERS:
        if f'"{marker}"' in blob or f": {marker}" in blob:
            raise ContractError(f"stub marker {marker!r} forbidden in envelope")
    # Extra stub spellings as JSON string values.
    for banned in ('"stub"', '"Stub"', '"STUB"', '"todo"'):
        if banned in blob:
            raise ContractError(f"stub marker {banned} forbidden in envelope")

    traces = doc.get("traces") or []
    if not isinstance(traces, list) or len(traces) < 2:
        raise ContractError("thin envelope: traces length < 2")
    for i, tr in enumerate(traces):
        if not isinstance(tr, dict):
            raise ContractError(f"thin envelope: traces[{i}] not object")
        channels = tr.get("channels")
        if not isinstance(channels, dict) or not channels:
            raise ContractError(f"thin envelope: traces[{i}].channels empty")
        for name, block in channels.items():
            if isinstance(block, dict) and "values" in block:
                vals = block["values"]
                if not isinstance(vals, list) or len(vals) == 0:
                    raise ContractError(
                        f"thin envelope: traces[{i}].channels.{name}.values empty"
                    )

    meta_top = doc.get("meta") if isinstance(doc.get("meta"), dict) else {}
    if meta_top.get("hardcoded_pass") is True or doc.get("hardcoded_pass") is True:
        raise ContractError("hardcoded_pass smell forbidden")

    op = str(doc.get("operator", "")).upper()
    if op in OPERATORS and not _axis_present(op, doc):
        hint = AXIS_REQUIREMENTS.get(op, "operator axis fields")
        raise ContractError(f"thin envelope: missing axis for {op} ({hint})")


def validate_envelope(
    doc: dict[str, Any],
    *,
    reject_thin: bool = True,
) -> dict[str, Any]:
    """Validate hard keys + enums; optionally reject thin/stub envelopes (default)."""
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
    # Honest inconclusive path (OPERATOR_TABLE / ONEPAGER): requires a reason;
    # inconclusive ≠ green. Otherwise role→verdict coupling is strict.
    if doc["expected_verdict"] == "inconclusive":
        reason = _inconclusive_reason(doc)
        if not reason:
            raise ContractError(
                "inconclusive requires meta.inconclusive_reason "
                "(top-level meta or traces[].meta)"
            )
    elif doc["control_role"] == "conforming" and doc["expected_verdict"] != "pass":
        raise ContractError("conforming must expected_verdict=pass")
    elif doc["control_role"] == "violating" and doc["expected_verdict"] != "fail":
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
    for banned in FORBIDDEN_SCORE_FIELDS:
        if banned in blob:
            raise ContractError(f"forbidden score field {banned}")
    if reject_thin:
        reject_thin_envelope(doc)
    return doc


def load_probe(path: str | Path, *, reject_thin: bool = True) -> dict[str, Any]:
    return validate_envelope(
        json.loads(Path(path).read_text(encoding="utf-8")),
        reject_thin=reject_thin,
    )


def parse_envelope(
    doc: dict[str, Any] | str | Path,
    *,
    reject_thin: bool = True,
) -> "ProbeEnvelope":
    """Validate and return a typed ProbeEnvelope dataclass."""
    from diptych.schema import ProbeEnvelope

    if isinstance(doc, (str, Path)):
        raw = json.loads(Path(doc).read_text(encoding="utf-8"))
    elif isinstance(doc, dict):
        raw = doc
    else:
        raise ContractError("parse_envelope expects dict, path, or str path")
    validated = validate_envelope(raw, reject_thin=reject_thin)
    return ProbeEnvelope.from_dict(validated)
