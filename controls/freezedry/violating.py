"""Handwritten violating control for FREEZEDRY — should fail."""
OPERATOR = "FREEZEDRY"
ROLE = "violating"
EXPECTED = "fail"


def describe() -> str:
    return "Handwritten violating twin for FREEZEDRY (protocol evidence)."
