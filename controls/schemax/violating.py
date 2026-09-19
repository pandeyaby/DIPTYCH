"""Handwritten violating control for SCHEMAX — should fail."""
OPERATOR = "SCHEMAX"
ROLE = "violating"
EXPECTED = "fail"


def describe() -> str:
    return "Handwritten violating twin for SCHEMAX (protocol evidence)."
