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
