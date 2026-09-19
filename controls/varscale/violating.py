"""Handwritten violating control for VARSCALE — should fail."""
OPERATOR = "VARSCALE"
ROLE = "violating"
EXPECTED = "fail"


def describe() -> str:
    return "Handwritten violating twin for VARSCALE (protocol evidence)."
