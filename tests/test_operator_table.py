"""Operator-table fidelity: specs + docs match diptych enums (no invented scores)."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Canonical coupling + graded-channel sketch from docs/adapters/OPERATOR_TABLE.md
# (must stay aligned with paper Table tab:ops and ops/*/spec.yaml).
EXPECTED_COUPLING = {
    "FREEZEDRY": "open_loop",
    "RESEED": "open_loop",
    "SCHEMAX": "open_loop",
    "SIGNFLIP": "open_loop",
    "SATEXTEND": "open_loop",
    "HISTSWAP": "open_loop",
    "TRAJSWAP": "crn_closed_loop",
    "VARSCALE": "crn_closed_loop",
}

# Substrings that must appear in OPERATOR_TABLE.md graded-channel cells.
EXPECTED_GRADED_MARKERS = {
    "FREEZEDRY": ("decision_fingerprint", "freeze_channels"),
    "RESEED": ("meta.seed", "stability"),
    "SCHEMAX": ("schema",),
    "SIGNFLIP": ("signflip_channel",),
    "SATEXTEND": ("sat_lo", "sat_hi"),
    "HISTSWAP": ("hist_splice_at",),
    "TRAJSWAP": ("trajectory", "closed_loop_residual"),
    "VARSCALE": ("var_scale",),
}


def _parse_spec_coupling(text: str) -> str:
    m = re.search(r"^coupling:\s*(\S+)\s*$", text, re.M)
    if not m:
        raise AssertionError("missing coupling: in spec")
    return m.group(1)


class TestOperatorTableFidelity(unittest.TestCase):
    def test_operators_tuple_matches_expected_set(self):
        from diptych import CRN_REQUIRED, OPERATORS

        self.assertEqual(set(OPERATORS), set(EXPECTED_COUPLING))
        self.assertEqual(set(CRN_REQUIRED), {"TRAJSWAP", "VARSCALE"})

    def test_ops_spec_yaml_couplings(self):
        from diptych import OPERATORS

        for op in OPERATORS:
            spec = (ROOT / "ops" / op.lower() / "spec.yaml").read_text(encoding="utf-8")
            self.assertIn(f"operator: {op}", spec)
            self.assertEqual(_parse_spec_coupling(spec), EXPECTED_COUPLING[op], op)

    def test_operator_table_md_couplings_and_channels(self):
        md = (ROOT / "docs" / "adapters" / "OPERATOR_TABLE.md").read_text(encoding="utf-8")
        for op, coupling in EXPECTED_COUPLING.items():
            # Table rows: | OP | open_loop | ...  or  | OP | **crn_closed_loop** | ...
            pat = rf"\|\s*{op}\s*\|\s*\*{{0,2}}{re.escape(coupling)}\*{{0,2}}\s*\|"
            self.assertRegex(md, pat, f"{op} coupling row")
            row = next(
                (ln for ln in md.splitlines() if re.match(rf"\|\s*{op}\s*\|", ln)),
                None,
            )
            self.assertIsNotNone(row, f"row for {op}")
            for marker in EXPECTED_GRADED_MARKERS[op]:
                self.assertIn(marker, row, f"{op} graded marker {marker}")

    def test_paper_tex_tab_ops_couplings_and_channels(self):
        tex = (ROOT / "paper" / "one-trace-is-not-enough.tex").read_text(encoding="utf-8")
        self.assertIn(r"\label{tab:ops}", tex)
        # Coupling enums appear as \texttt{open\_loop} / \texttt{crn\_closed\_loop}
        self.assertIn(r"\texttt{open\_loop}", tex)
        self.assertIn(r"\texttt{crn\_closed\_loop}", tex)
        # Each operator row (textsc name) near its coupling — spot-check graded markers.
        for op, markers in EXPECTED_GRADED_MARKERS.items():
            # FREEZEDRY -> FreezeDry in textsc
            pretty = {
                "SIGNFLIP": "SignFlip",
                "TRAJSWAP": "TrajSwap",
                "VARSCALE": "VarScale",
                "SATEXTEND": "SatExtend",
                "HISTSWAP": "HistSwap",
                "FREEZEDRY": "FreezeDry",
                "RESEED": "Reseed",
                "SCHEMAX": "SchemaX",
            }[op]
            self.assertIn(rf"\textsc{{{pretty}}}", tex, op)
            for marker in markers:
                # TeX may escape underscores
                tex_marker = marker.replace("_", r"\_")
                self.assertTrue(
                    marker in tex or tex_marker in tex,
                    f"paper missing graded marker {marker} for {op}",
                )
            coupling = EXPECTED_COUPLING[op]
            tex_coupling = coupling.replace("_", r"\_")
            self.assertIn(rf"\texttt{{{tex_coupling}}}", tex)


if __name__ == "__main__":
    unittest.main()
