"""Handwritten violating control for SIGNFLIP — should fail."""
OPERATOR = "SIGNFLIP"
ROLE = "violating"
EXPECTED = "fail"


def describe() -> str:
    return "Handwritten violating twin for SIGNFLIP (protocol evidence)."
