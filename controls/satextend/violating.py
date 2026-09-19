"""Handwritten violating control for SATEXTEND — should fail."""
OPERATOR = "SATEXTEND"
ROLE = "violating"
EXPECTED = "fail"


def describe() -> str:
    return "Handwritten violating twin for SATEXTEND (protocol evidence)."
