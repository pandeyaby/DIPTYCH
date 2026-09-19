"""Handwritten violating control for RESEED — should fail."""
OPERATOR = "RESEED"
ROLE = "violating"
EXPECTED = "fail"


def describe() -> str:
    return "Handwritten violating twin for RESEED (protocol evidence)."
