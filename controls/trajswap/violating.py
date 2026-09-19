"""Handwritten violating control for TRAJSWAP — should fail."""
OPERATOR = "TRAJSWAP"
ROLE = "violating"
EXPECTED = "fail"


def describe() -> str:
    return "Handwritten violating twin for TRAJSWAP (protocol evidence)."
