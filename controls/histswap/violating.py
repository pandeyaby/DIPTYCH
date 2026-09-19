"""Handwritten violating control for HISTSWAP — should fail."""
OPERATOR = "HISTSWAP"
ROLE = "violating"
EXPECTED = "fail"


def describe() -> str:
    return "Handwritten violating twin for HISTSWAP (protocol evidence)."
