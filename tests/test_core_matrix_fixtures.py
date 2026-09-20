"""CI: coverage/matrix.json diptych_core row matches fixture-derived grades.

Harness-only (#19). Adapter pin columns (zeroday/aomb) must stay pin truth —
never invented from partial ZeroDay/AOMB fixture trees.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MATRIX = ROOT / "coverage" / "matrix.json"
CASSETTE = ROOT / "examples" / "fixtures" / "cassette"
PROBES = ROOT / "diptych-probes"
ZERODAY_FIX = ROOT / "examples" / "fixtures" / "zeroday"
AOMB_FIX = ROOT / "examples" / "fixtures" / "aomb"


def _cli_env() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)
    return env


class TestCoreMatrixFromFixtures(unittest.TestCase):
    def test_cassette_and_probes_derive_full8_green(self):
        from diptych import OPERATORS
        from diptych.matrix import derive_core_cells

        derived = derive_core_cells([CASSETTE, PROBES])
        for op in OPERATORS:
            cell = derived["operators"][op]
            self.assertEqual(cell["diptych_core"], "green", op)
            self.assertTrue(cell["axis_power"], op)
            self.assertGreaterEqual(derived["evidence"][op]["pair_count"], 1, op)

    def test_live_matrix_diptych_core_matches_fixture_grades(self):
        """Drift gate: live matrix diptych_core+axis_power == fixture grades."""
        from diptych.matrix import verify_matrix

        errors = verify_matrix(MATRIX)
        self.assertEqual(errors, [], errors)

    def test_adapter_fixture_trees_do_not_invent_core_greens(self):
        """Partial zeroday/aomb fixture trees must not yield diptych_core greens."""
        from diptych import OPERATORS
        from diptych.matrix import derive_core_cells

        derived = derive_core_cells([ZERODAY_FIX, AOMB_FIX])
        for op in OPERATORS:
            cell = derived["operators"][op]
            self.assertEqual(cell["diptych_core"], "pending", op)
            self.assertFalse(cell["axis_power"], op)
            self.assertEqual(derived["evidence"][op]["pair_count"], 0, op)

    def test_refresh_preserves_adapter_pin_columns(self):
        from diptych.gates import _adapter_cell
        from diptych.matrix import build_matrix_document, load_matrix

        live = load_matrix(MATRIX)
        doc = build_matrix_document(existing=live)
        want_z = _adapter_cell("zeroday")
        want_a = _adapter_cell("aomb")
        for op, cell in doc["operators"].items():
            self.assertEqual(cell["zeroday"], want_z, op)
            self.assertEqual(cell["aomb"], want_a, op)
            self.assertEqual(cell["diptych_core"], "green", op)
            self.assertTrue(cell["axis_power"], op)

    def test_check_cli_exits_zero_on_live_matrix(self):
        proc = subprocess.run(
            [sys.executable, "-m", "diptych.matrix", "--check"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=False,
            env=_cli_env(),
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("MATRIX CHECK OK", proc.stdout)

    def test_check_cli_detects_drift(self):
        from diptych.matrix import load_matrix

        live = load_matrix(MATRIX)
        with tempfile.TemporaryDirectory() as tmp:
            broken = Path(tmp) / "matrix.json"
            live["operators"]["RESEED"]["diptych_core"] = "pending"
            live["operators"]["RESEED"]["axis_power"] = False
            broken.write_text(json.dumps(live, indent=2) + "\n", encoding="utf-8")
            proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "diptych.matrix",
                    "--check",
                    "--matrix",
                    str(broken),
                ],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                check=False,
                env=_cli_env(),
            )
            self.assertNotEqual(proc.returncode, 0, proc.stdout)
            self.assertIn("MATRIX CHECK FAIL", proc.stdout)
            self.assertIn("RESEED.diptych_core", proc.stdout)

    def test_refresh_script_write_roundtrip(self):
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "refresh_core_matrix.py"), "--check"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=False,
            env=_cli_env(),
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)


if __name__ == "__main__":
    unittest.main()
