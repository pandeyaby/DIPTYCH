"""Typed diptych_schema 0.2 envelopes (CONTRACT.md / OPERATOR_TABLE.md).

Dataclasses + Literal/TypedDict aliases for the public harness API.
No product scores — schema fields only.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal, NotRequired, TypedDict

# --- Enumerations (CONTRACT v0.2) ---

OperatorName = Literal[
    "SIGNFLIP",
    "TRAJSWAP",
    "VARSCALE",
    "SATEXTEND",
    "HISTSWAP",
    "FREEZEDRY",
    "RESEED",
    "SCHEMAX",
]
Coupling = Literal["open_loop", "crn_closed_loop"]
ControlRole = Literal["conforming", "violating"]
Verdict = Literal["pass", "fail", "inconclusive"]
SourceName = Literal["diptych_core", "zeroday", "aomb"]
HorizonUnit = Literal["steps", "ms", "events"]

OPERATOR_ENUM: frozenset[str] = frozenset(
    {
        "SIGNFLIP",
        "TRAJSWAP",
        "VARSCALE",
        "SATEXTEND",
        "HISTSWAP",
        "FREEZEDRY",
        "RESEED",
        "SCHEMAX",
    }
)
COUPLING_ENUM: frozenset[str] = frozenset({"open_loop", "crn_closed_loop"})
CONTROL_ROLE_ENUM: frozenset[str] = frozenset({"conforming", "violating"})
VERDICT_ENUM: frozenset[str] = frozenset({"pass", "fail", "inconclusive"})
SOURCE_ENUM: frozenset[str] = frozenset({"diptych_core", "zeroday", "aomb"})

# Per-operator axis presence (GATING / OPERATOR_TABLE) — used by thin-envelope rejector.
AXIS_REQUIREMENTS: dict[str, str] = {
    "RESEED": "channels.stability.values + meta.seed + meta.epsilon (seeds differ)",
    "SCHEMAX": "channels.schema.keys or meta.required_schema_keys",
    "FREEZEDRY": "channels.graded + meta.freeze_channels + meta.decision_fingerprint",
    "SIGNFLIP": "meta.signflip_channel + channels.<target>.values",
    "SATEXTEND": "meta.sat_lo/sat_hi + channels.<sat_channel>.values",
    "HISTSWAP": "channels.history + meta.hist_splice_at",
    "TRAJSWAP": "channels.trajectory + channels.closed_loop_residual (CRN)",
    "VARSCALE": "channels.variance_proxy + meta.var_scale (CRN)",
}


class HorizonDict(TypedDict, total=False):
    unit: HorizonUnit
    length: int | float


class ChannelBlock(TypedDict, total=False):
    values: list[Any]
    keys: list[Any]


class TraceMetaDict(TypedDict, total=False):
    seed: Any
    crn_stream_id: str
    crn_closed_loop: bool
    epsilon: float
    config_id: str
    freeze_channels: list[str]
    decision_fingerprint: str
    signflip_channel: str
    signflip_eps: float
    sign_normalized_fingerprint: str
    sat_channel: str
    sat_lo: float
    sat_hi: float
    legal_lo: float
    legal_hi: float
    hist_splice_at: int
    history_corrupt: bool
    crn_drift: float
    residual_bound: float
    var_scale: float
    var_scale_bound: float
    crn_mean: float
    mean_match_eps: float
    required_schema_keys: list[str]
    inconclusive_reason: str


class TraceDict(TypedDict):
    trace_id: str
    channels: dict[str, Any]
    meta: TraceMetaDict
    events: NotRequired[list[Any]]


class ProbeEnvelopeDict(TypedDict):
    """TypedDict mirror of CONTRACT v0.2 hard + common optional keys."""

    diptych_schema: str
    source: SourceName
    operator: OperatorName
    coupling: Coupling
    probe_id: str
    control_role: ControlRole
    traces: list[TraceDict]
    expected_verdict: Verdict
    horizon: NotRequired[HorizonDict]
    fixture_id: NotRequired[str]
    crn_closed_loop: NotRequired[bool]
    meta: NotRequired[dict[str, Any]]


@dataclass
class Horizon:
    unit: str = "steps"
    length: int | float = 0

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> Horizon | None:
        if not raw:
            return None
        return cls(unit=str(raw.get("unit", "steps")), length=raw.get("length", 0))

    def to_dict(self) -> dict[str, Any]:
        return {"unit": self.unit, "length": self.length}


@dataclass
class Trace:
    trace_id: str
    channels: dict[str, Any]
    meta: dict[str, Any]
    events: list[Any] = field(default_factory=list)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Trace:
        return cls(
            trace_id=str(raw["trace_id"]),
            channels=dict(raw.get("channels") or {}),
            meta=dict(raw.get("meta") or {}),
            events=list(raw.get("events") or []),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "events": list(self.events),
            "channels": dict(self.channels),
            "meta": dict(self.meta),
        }


@dataclass
class ProbeEnvelope:
    """Runtime dataclass for a CONTRACT v0.2 probe-pair envelope."""

    diptych_schema: str
    source: str
    operator: str
    coupling: str
    probe_id: str
    control_role: str
    traces: list[Trace]
    expected_verdict: str
    horizon: Horizon | None = None
    fixture_id: str | None = None
    crn_closed_loop: bool | None = None
    meta: dict[str, Any] = field(default_factory=dict)
    # Preserve unknown top-level keys for round-trip fidelity.
    extras: dict[str, Any] = field(default_factory=dict)

    _KNOWN = frozenset(
        {
            "diptych_schema",
            "source",
            "operator",
            "coupling",
            "probe_id",
            "control_role",
            "traces",
            "expected_verdict",
            "horizon",
            "fixture_id",
            "crn_closed_loop",
            "meta",
        }
    )

    @classmethod
    def from_dict(cls, doc: dict[str, Any]) -> ProbeEnvelope:
        traces = [Trace.from_dict(t) for t in doc["traces"]]
        extras = {k: v for k, v in doc.items() if k not in cls._KNOWN}
        return cls(
            diptych_schema=str(doc["diptych_schema"]),
            source=str(doc["source"]),
            operator=str(doc["operator"]).upper(),
            coupling=str(doc["coupling"]),
            probe_id=str(doc["probe_id"]),
            control_role=str(doc["control_role"]),
            traces=traces,
            expected_verdict=str(doc["expected_verdict"]),
            horizon=Horizon.from_dict(doc.get("horizon") if isinstance(doc.get("horizon"), dict) else None),
            fixture_id=doc.get("fixture_id"),
            crn_closed_loop=doc.get("crn_closed_loop"),
            meta=dict(doc.get("meta") or {}),
            extras=extras,
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "diptych_schema": self.diptych_schema,
            "source": self.source,
            "operator": self.operator,
            "coupling": self.coupling,
            "probe_id": self.probe_id,
            "control_role": self.control_role,
            "traces": [t.to_dict() for t in self.traces],
            "expected_verdict": self.expected_verdict,
        }
        if self.horizon is not None:
            out["horizon"] = self.horizon.to_dict()
        if self.fixture_id is not None:
            out["fixture_id"] = self.fixture_id
        if self.crn_closed_loop is not None:
            out["crn_closed_loop"] = self.crn_closed_loop
        if self.meta:
            out["meta"] = dict(self.meta)
        out.update(self.extras)
        return out

    def to_typed_dict(self) -> ProbeEnvelopeDict:
        """Best-effort TypedDict view (runtime is still a plain dict)."""
        return self.to_dict()  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Executable CONTRACT v0.2 JSON Schema (adapters validate without prose docs)
# ---------------------------------------------------------------------------

import argparse
import json
import sys
from pathlib import Path

from diptych import (
    CONTROL_ROLES,
    COUPLINGS,
    CRN_REQUIRED,
    OPERATORS,
    SCHEMA,
    SOURCES,
    VERDICTS,
)

# Repo-relative path adapters / CI consume. Also: diptych/schema/v0_2.json alias.
SCHEMA_JSON_RELPATH = Path("docs/adapters/diptych_schema_0.2.json")
SCHEMA_JSON_ALT_RELPATH = Path("diptych/schema/v0_2.json")

# Score / smell keys adapters must not invent (CONTRACT / GATING).
REJECTED_TOP_LEVEL_KEYS: tuple[str, ...] = (
    "auroc",
    "AUROC",
    "lab_auroc",
    "model_grade",
    "hardcoded_pass",
)

HARD_KEYS: tuple[str, ...] = (
    "diptych_schema",
    "source",
    "operator",
    "coupling",
    "probe_id",
    "control_role",
    "traces",
    "expected_verdict",
)

HORIZON_UNITS: tuple[str, ...] = ("steps", "ms", "events")

# Graded / axis channels called out in OPERATOR_TABLE (structural schema hints).
_OPERATOR_CHANNEL_IF_THEN: tuple[tuple[str, dict[str, Any]], ...] = (
    (
        "RESEED",
        {
            "type": "object",
            "required": ["stability"],
            "properties": {
                "stability": {
                    "type": "object",
                    "required": ["values"],
                    "properties": {
                        "values": {"type": "array", "minItems": 1},
                    },
                }
            },
        },
    ),
    (
        "FREEZEDRY",
        {
            "type": "object",
            "required": ["graded"],
            "properties": {
                "graded": {
                    "type": "object",
                    "required": ["values"],
                    "properties": {
                        "values": {"type": "array", "minItems": 1},
                    },
                }
            },
        },
    ),
    (
        "HISTSWAP",
        {
            "type": "object",
            "required": ["history"],
            "properties": {
                "history": {
                    "type": "object",
                    "required": ["values"],
                    "properties": {
                        "values": {"type": "array", "minItems": 1},
                    },
                }
            },
        },
    ),
    (
        "TRAJSWAP",
        {
            "type": "object",
            "required": ["trajectory", "closed_loop_residual"],
            "properties": {
                "trajectory": {
                    "type": "object",
                    "required": ["values"],
                    "properties": {
                        "values": {"type": "array", "minItems": 1},
                    },
                },
                "closed_loop_residual": {
                    "type": "object",
                    "required": ["values"],
                    "properties": {
                        "values": {"type": "array", "minItems": 1},
                    },
                },
            },
        },
    ),
    (
        "VARSCALE",
        {
            "type": "object",
            "required": ["variance_proxy"],
            "properties": {
                "variance_proxy": {
                    "type": "object",
                    "required": ["values"],
                    "properties": {
                        "values": {"type": "array", "minItems": 1},
                    },
                }
            },
        },
    ),
    (
        "SCHEMAX",
        {
            "type": "object",
            "required": ["schema"],
            "properties": {
                "schema": {
                    "type": "object",
                    "required": ["keys"],
                    "properties": {
                        "keys": {"type": "array", "minItems": 1},
                    },
                }
            },
        },
    ),
)


class SchemaError(ValueError):
    """JSON Schema / CONTRACT schema-check failure."""


def build_json_schema() -> dict[str, Any]:
    """Build Draft 2020-12 schema from typed ProbeEnvelope / OPERATORS / COUPLINGS.

    Generated from live enums — do not hand-edit the committed JSON; regenerate
    via ``python -m diptych.schema --dump-schema``.
    """
    operators = list(OPERATORS)
    couplings = sorted(COUPLINGS)
    sources = sorted(SOURCES)
    roles = sorted(CONTROL_ROLES)
    verdicts = sorted(VERDICTS)
    crn_ops = sorted(CRN_REQUIRED)

    channel_block = {
        "type": "object",
        "properties": {
            "values": {"type": "array", "minItems": 1},
            "keys": {"type": "array", "minItems": 1},
        },
        "additionalProperties": True,
    }

    trace = {
        "type": "object",
        "required": ["trace_id", "channels", "meta"],
        "properties": {
            "trace_id": {"type": "string", "minLength": 1},
            "events": {"type": "array"},
            "channels": {
                "type": "object",
                "minProperties": 1,
                "additionalProperties": channel_block,
            },
            "meta": {
                "type": "object",
                "required": ["seed"],
                "properties": {
                    "seed": True,
                    "crn_stream_id": {"type": "string"},
                    "crn_closed_loop": {"type": "boolean"},
                    "epsilon": {"type": "number"},
                    "inconclusive_reason": {"type": "string", "minLength": 1},
                },
                "additionalProperties": True,
            },
        },
        "additionalProperties": True,
    }

    forbidden_not = {
        "anyOf": [{"required": [k]} for k in REJECTED_TOP_LEVEL_KEYS]
    }

    op_channel_rules = [
        {
            "if": {"properties": {"operator": {"const": op}}, "required": ["operator"]},
            "then": {
                "properties": {
                    "traces": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"channels": ch_schema},
                        },
                    }
                }
            },
        }
        for op, ch_schema in _OPERATOR_CHANNEL_IF_THEN
    ]

    crn_rules = [
        {
            "if": {"properties": {"operator": {"const": op}}, "required": ["operator"]},
            "then": {
                "properties": {"coupling": {"const": "crn_closed_loop"}},
                "required": ["coupling"],
            },
        }
        for op in crn_ops
    ]

    schema: dict[str, Any] = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://github.com/pandeyaby/DIPTYCH/docs/adapters/diptych_schema_0.2.json",
        "title": "DIPTYCH CONTRACT v0.2 probe-pair envelope",
        "description": (
            "Executable CONTRACT for adapter emitters. Enums are generated from "
            "diptych.OPERATORS / COUPLINGS / CONTROL_ROLES / VERDICTS / SOURCES. "
            "Rejected top-level keys include invented score fields (auroc, etc.). "
            "Thin / stub / axis semantics are additionally enforced by "
            "diptych.contract.validate_envelope (also on python -m diptych.grade)."
        ),
        "type": "object",
        "required": list(HARD_KEYS),
        "properties": {
            "diptych_schema": {"const": SCHEMA},
            "source": {"type": "string", "enum": sources},
            "operator": {"type": "string", "enum": operators},
            "coupling": {"type": "string", "enum": couplings},
            "probe_id": {"type": "string", "minLength": 1},
            "control_role": {"type": "string", "enum": roles},
            "expected_verdict": {"type": "string", "enum": verdicts},
            "traces": {
                "type": "array",
                "minItems": 1,
                "items": trace,
            },
            "horizon": {
                "type": "object",
                "required": ["unit", "length"],
                "properties": {
                    "unit": {"type": "string", "enum": list(HORIZON_UNITS)},
                    "length": {"type": "number", "minimum": 0},
                },
                "additionalProperties": False,
            },
            "fixture_id": {"type": "string"},
            "crn_closed_loop": {"type": "boolean"},
            "meta": {"type": "object"},
        },
        # Allow forward-compatible optional keys, but reject score smells via `not`.
        "additionalProperties": True,
        "not": forbidden_not,
        "allOf": [
            *crn_rules,
            *op_channel_rules,
            {
                "if": {
                    "properties": {"expected_verdict": {"const": "inconclusive"}},
                    "required": ["expected_verdict"],
                },
                "then": {
                    "properties": {
                        "traces": {"type": "array", "minItems": 1},
                    }
                },
                "else": {
                    "properties": {
                        "traces": {"type": "array", "minItems": 2},
                    }
                },
            },
        ],
        "$defs": {
            "channel_block": channel_block,
            "trace": trace,
            "rejected_keys": {
                "description": "Top-level keys adapters must not emit",
                "enum": list(REJECTED_TOP_LEVEL_KEYS),
            },
            "operators": {"enum": operators},
            "couplings": {"enum": couplings},
        },
    }
    return schema


def repo_root() -> Path:
    """Return repository root (parent of the ``diptych`` package).

    ``__file__`` lives at ``diptych/schema/__init__.py`` → parents[2] is repo root.
    """
    return Path(__file__).resolve().parents[2]


def schema_json_path() -> Path:
    """Absolute path to the committed CONTRACT JSON Schema artifact."""
    return repo_root() / SCHEMA_JSON_RELPATH


def dump_json_schema(
    path: str | Path | None = None,
    *,
    also_alt: bool = True,
) -> Path:
    """Write generated schema JSON to ``path`` (default: committed docs path)."""
    target = Path(path) if path is not None else schema_json_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(build_json_schema(), indent=2, sort_keys=False) + "\n"
    target.write_text(text, encoding="utf-8")
    if also_alt and path is None:
        alt = repo_root() / SCHEMA_JSON_ALT_RELPATH
        alt.parent.mkdir(parents=True, exist_ok=True)
        alt.write_text(text, encoding="utf-8")
    return target


def load_committed_schema() -> dict[str, Any]:
    path = schema_json_path()
    if not path.is_file():
        raise SchemaError(f"committed schema missing: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def assert_committed_schema_fresh() -> None:
    """Fail if docs/adapters/diptych_schema_0.2.json drifts from build_json_schema()."""
    committed = load_committed_schema()
    generated = build_json_schema()
    if committed != generated:
        raise SchemaError(
            f"{SCHEMA_JSON_RELPATH} is stale; run: "
            "python -m diptych.schema --dump-schema"
        )


# --- Minimal Draft 2020-12 evaluator (stdlib-only; adapter-facing subset) ---


def _resolve_ref(root: dict[str, Any], ref: str) -> dict[str, Any]:
    if not ref.startswith("#/"):
        raise SchemaError(f"unsupported $ref: {ref}")
    node: Any = root
    for part in ref[2:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        if not isinstance(node, dict) or part not in node:
            raise SchemaError(f"unresolved $ref: {ref}")
        node = node[part]
    if not isinstance(node, dict):
        raise SchemaError(f"$ref target not object: {ref}")
    return node


def _validate_schema(
    instance: Any,
    schema: dict[str, Any] | bool,
    *,
    root: dict[str, Any],
    path: str = "$",
) -> None:
    if schema is True:
        return
    if schema is False:
        raise SchemaError(f"{path}: False schema")
    if "$ref" in schema:
        _validate_schema(instance, _resolve_ref(root, schema["$ref"]), root=root, path=path)
        return

    if "type" in schema:
        expected = schema["type"]
        types = expected if isinstance(expected, list) else [expected]
        ok = False
        for t in types:
            if t == "object" and isinstance(instance, dict):
                ok = True
            elif t == "array" and isinstance(instance, list):
                ok = True
            elif t == "string" and isinstance(instance, str):
                ok = True
            elif t == "boolean" and isinstance(instance, bool):
                ok = True
            elif t == "number" and isinstance(instance, (int, float)) and not isinstance(
                instance, bool
            ):
                ok = True
            elif t == "integer" and isinstance(instance, int) and not isinstance(
                instance, bool
            ):
                ok = True
            elif t == "null" and instance is None:
                ok = True
        if not ok:
            raise SchemaError(f"{path}: expected type {expected}, got {type(instance).__name__}")

    if "const" in schema and instance != schema["const"]:
        raise SchemaError(f"{path}: expected const {schema['const']!r}, got {instance!r}")

    if "enum" in schema and instance not in schema["enum"]:
        raise SchemaError(f"{path}: {instance!r} not in enum {schema['enum']}")

    if "minLength" in schema and isinstance(instance, str):
        if len(instance) < schema["minLength"]:
            raise SchemaError(f"{path}: string shorter than minLength {schema['minLength']}")

    if "minimum" in schema and isinstance(instance, (int, float)) and not isinstance(
        instance, bool
    ):
        if instance < schema["minimum"]:
            raise SchemaError(f"{path}: {instance} < minimum {schema['minimum']}")

    if "not" in schema:
        try:
            _validate_schema(instance, schema["not"], root=root, path=path)
        except SchemaError:
            pass
        else:
            raise SchemaError(f"{path}: matches forbidden 'not' schema")

    if "anyOf" in schema:
        errors: list[str] = []
        for i, sub in enumerate(schema["anyOf"]):
            try:
                _validate_schema(instance, sub, root=root, path=f"{path}/anyOf[{i}]")
                break
            except SchemaError as exc:
                errors.append(str(exc))
        else:
            raise SchemaError(f"{path}: anyOf failed ({'; '.join(errors[:3])})")

    if "allOf" in schema:
        for i, sub in enumerate(schema["allOf"]):
            _validate_schema(instance, sub, root=root, path=f"{path}/allOf[{i}]")

    if "if" in schema:
        try:
            _validate_schema(instance, schema["if"], root=root, path=f"{path}/if")
            matched = True
        except SchemaError:
            matched = False
        if matched and "then" in schema:
            _validate_schema(instance, schema["then"], root=root, path=f"{path}/then")
        if not matched and "else" in schema:
            _validate_schema(instance, schema["else"], root=root, path=f"{path}/else")

    if isinstance(instance, dict):
        required = schema.get("required") or []
        for key in required:
            if key not in instance:
                raise SchemaError(f"{path}: missing required property {key!r}")
        props = schema.get("properties") or {}
        for key, val in instance.items():
            if key in props:
                _validate_schema(
                    val, props[key], root=root, path=f"{path}.{key}"
                )
            elif "additionalProperties" in schema:
                add = schema["additionalProperties"]
                if add is False:
                    raise SchemaError(f"{path}: additional property {key!r} forbidden")
                if isinstance(add, dict):
                    _validate_schema(val, add, root=root, path=f"{path}.{key}")
        if "minProperties" in schema and len(instance) < schema["minProperties"]:
            raise SchemaError(
                f"{path}: fewer than minProperties {schema['minProperties']}"
            )

    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < schema["minItems"]:
            raise SchemaError(f"{path}: fewer than minItems {schema['minItems']}")
        if "items" in schema:
            item_schema = schema["items"]
            for i, item in enumerate(instance):
                _validate_schema(item, item_schema, root=root, path=f"{path}[{i}]")


def validate_against_schema(
    doc: dict[str, Any],
    schema: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate ``doc`` against the CONTRACT JSON Schema (structure/enums only)."""
    if not isinstance(doc, dict):
        raise SchemaError("envelope must be object")
    sch = schema if schema is not None else build_json_schema()
    _validate_schema(doc, sch, root=sch, path="$")
    return doc


def check_envelope(doc: dict[str, Any]) -> dict[str, Any]:
    """Full CONTRACT check: JSON Schema + ``validate_envelope`` (thin/stub/axis)."""
    from diptych.contract import validate_envelope

    validate_against_schema(doc)
    return validate_envelope(doc)


def check_path(path: str | Path) -> dict[str, Any]:
    """Load JSON from ``path`` and run :func:`check_envelope`."""
    p = Path(path)
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SchemaError(f"invalid JSON in {p}: {exc}") from exc
    return check_envelope(raw)


def main(argv: list[str] | None = None) -> int:
    """CLI: ``python -m diptych.schema --check PATH`` / ``--dump-schema``."""
    p = argparse.ArgumentParser(
        prog="python -m diptych.schema",
        description=(
            "DIPTYCH CONTRACT v0.2 JSON Schema: dump the executable schema or "
            "check probe envelopes (structure + thin/stub/axis via contract)."
        ),
    )
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--check",
        metavar="PATH",
        help="Validate a probe.json (or fail loudly). Also covered on grade path.",
    )
    mode.add_argument(
        "--dump-schema",
        nargs="?",
        const="",
        metavar="OUT",
        help=(
            "Write CONTRACT JSON Schema (default: docs/adapters/diptych_schema_0.2.json "
            "+ diptych/schema/v0_2.json). Pass OUT to write only that path. "
            "Use '-' for stdout."
        ),
    )
    mode.add_argument(
        "--check-fresh",
        action="store_true",
        help="Fail if committed docs/adapters/diptych_schema_0.2.json drifts from code",
    )
    args = p.parse_args(argv)

    if args.check_fresh:
        try:
            assert_committed_schema_fresh()
        except SchemaError as exc:
            print(f"SCHEMA FRESH FAIL: {exc}", file=sys.stderr)
            return 1
        print(f"SCHEMA FRESH OK ({SCHEMA_JSON_RELPATH})")
        return 0

    if args.dump_schema is not None:
        if args.dump_schema == "-":
            sys.stdout.write(json.dumps(build_json_schema(), indent=2) + "\n")
            return 0
        if args.dump_schema == "":
            written = dump_json_schema()
            alt = repo_root() / SCHEMA_JSON_ALT_RELPATH
            print(f"wrote {written}")
            print(f"wrote {alt}")
            return 0
        written = dump_json_schema(args.dump_schema, also_alt=False)
        print(f"wrote {written}")
        return 0

    # --check
    path = Path(args.check)
    try:
        if path.is_dir():
            files = sorted(path.rglob("probe.json")) + sorted(path.glob("*.json"))
            # De-dupe while preserving order
            seen: set[Path] = set()
            uniq: list[Path] = []
            for f in files:
                rp = f.resolve()
                if rp not in seen and f.is_file():
                    seen.add(rp)
                    uniq.append(f)
            if not uniq:
                print(f"SCHEMA CHECK FAIL: no JSON under {path}", file=sys.stderr)
                return 2
            failures = 0
            for f in uniq:
                try:
                    check_path(f)
                    print(f"OK  {f}")
                except (SchemaError, Exception) as exc:
                    # ContractError is a ValueError subclass — catch broadly for CLI.
                    from diptych.contract import ContractError

                    if not isinstance(exc, (SchemaError, ContractError)):
                        raise
                    print(f"FAIL {f}: {exc}", file=sys.stderr)
                    failures += 1
            if failures:
                print(f"SCHEMA CHECK FAIL ({failures}/{len(uniq)})", file=sys.stderr)
                return 1
            print(f"SCHEMA CHECK OK ({len(uniq)} envelopes)")
            return 0
        check_path(path)
    except Exception as exc:
        from diptych.contract import ContractError

        if isinstance(exc, (SchemaError, ContractError)):
            print(f"SCHEMA CHECK FAIL: {exc}", file=sys.stderr)
            return 1
        raise
    print(f"SCHEMA CHECK OK ({path})")
    return 0


