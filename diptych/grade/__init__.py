"""Substantive graders + cassette/fixture CLI for DIPTYCH operators.

Stranger / adapter path (no product imports)::

    python -m diptych.grade --input path/to/probe.json
    python -m diptych.grade --input path/to/fixtures/dir --with-axis-mutate
"""
from __future__ import annotations

from diptych.grade._lib import (
    GRADE_SCHEMA,
    GRADERS,
    GradeResult,
    build_grade_report,
    collect_probe_paths,
    comparability_reason,
    grade_document,
    grade_freezedry,
    grade_histswap,
    grade_reseed,
    grade_satextend,
    grade_schemax,
    grade_signflip,
    grade_trajswap,
    grade_varscale,
    main,
)

__all__ = [
    "GRADE_SCHEMA",
    "GRADERS",
    "GradeResult",
    "build_grade_report",
    "collect_probe_paths",
    "comparability_reason",
    "grade_document",
    "grade_freezedry",
    "grade_histswap",
    "grade_reseed",
    "grade_satextend",
    "grade_schemax",
    "grade_signflip",
    "grade_trajswap",
    "grade_varscale",
    "main",
]
