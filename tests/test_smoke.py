"""Unified stranger smoke CLI: public surface in one command."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _env() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)
    return env


class TestSmokeReport(unittest.TestCase):
    def test_run_smoke_ok_on_repo_fixtures(self):
        from diptych import OPERATORS, SCHEMA
        from diptych.smoke import (
            SMOKE_REQUIRED_KEYS,
            SMOKE_SCHEMA,
            SMOKE_STEP_IDS,
            run_smoke,
        )

        report = run_smoke()
        for key in SMOKE_REQUIRED_KEYS:
            self.assertIn(key, report, key)
        self.assertEqual(report["smoke_schema"], SMOKE_SCHEMA)
        self.assertEqual(report["diptych_schema"], SCHEMA)
        self.assertTrue(report["ok"], report.get("failures"))
        self.assertEqual(report["failures"], [])
        self.assertEqual(report["step_count"], len(SMOKE_STEP_IDS))

        ids = [s["id"] for s in report["steps"]]
        self.assertEqual(ids, list(SMOKE_STEP_IDS))
        for step in report["steps"]:
            self.assertTrue(step["ok"], step)

        # Cassette graded; thin fully rejected (inverted grade ok).
        by_id = {s["id"]: s for s in report["steps"]}
        cassette = by_id["grade_cassette"]["detail"]
        self.assertGreater(cassette["graded"], 0)
        self.assertEqual(cassette["rejected"], 0)
        thin = by_id["thin_reject"]["detail"]
        self.assertGreater(thin["rejected"], 0)
        self.assertEqual(thin["graded"], 0)
        self.assertEqual(thin["rejected"], thin["count"])
        poc = by_id["poc_json"]["detail"]
        self.assertEqual(poc["operator_count"], len(OPERATORS))

        # No invented score fields.
        blob = json.dumps(report).lower()
        self.assertNotIn('"auroc"', blob)
        self.assertNotIn('"lab_auroc"', blob)
        self.assertNotIn('"model_grade"', blob)

    def test_cli_python_m_diptych_exit_zero(self):
        proc = subprocess.run(
            [sys.executable, "-m", "diptych"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=False,
            env=_env(),
        )
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        self.assertIn("SMOKE OK", proc.stdout)

    def test_cli_smoke_json_stdout(self):
        proc = subprocess.run(
            [sys.executable, "-m", "diptych", "smoke", "--json", "--stdout-only"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=False,
            env=_env(),
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        report = json.loads(proc.stdout)
        self.assertTrue(report["ok"])
        self.assertEqual(report["smoke_schema"], "1.4")
        self.assertEqual(len(report["steps"]), 9)

    def test_cli_smoke_subcommand_and_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "smoke.json"
            proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "diptych",
                    "smoke",
                    "--json",
                    "--out",
                    str(out),
                    "--quiet",
                ],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                check=False,
                env=_env(),
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertTrue(out.is_file())
            report = json.loads(out.read_text(encoding="utf-8"))
            self.assertTrue(report["ok"])

    def test_existing_module_clis_still_importable(self):
        """Keep poc / grade / matrix / schema module entrypoints working."""
        from diptych import grade, matrix, poc, schema, smoke

        self.assertTrue(callable(poc.main))
        self.assertTrue(callable(grade.main))
        self.assertTrue(callable(matrix.main))
        self.assertTrue(callable(schema.main))
        self.assertTrue(callable(smoke.main))


    def test_smoke_report_includes_pins_section(self):
        from diptych.smoke import STEP_PINS_CHECK, run_smoke

        report = run_smoke()
        by_id = {s["id"]: s for s in report["steps"]}
        self.assertIn(STEP_PINS_CHECK, by_id)
        pins = by_id[STEP_PINS_CHECK]
        self.assertTrue(pins["ok"], msg=pins)
        detail = pins["detail"]
        self.assertEqual(detail["exit_code"], 0)
        self.assertIn("zeroday_short", detail)
        self.assertIn("aomb_short", detail)
        self.assertEqual(len(detail["zeroday_short"]), 8)
        self.assertEqual(len(detail["aomb_short"]), 7)

    def test_smoke_fails_on_pin_mismatch(self):
        from unittest.mock import patch

        from diptych.pins import EXIT_MISMATCH
        from diptych.smoke import STEP_PINS_CHECK, run_smoke

        def boom():
            return EXIT_MISMATCH, ["pins mismatch (test)"], None

        with patch("diptych.smoke.check_paths", side_effect=lambda: boom()):
            report = run_smoke()
        self.assertFalse(report["ok"])
        self.assertIn(STEP_PINS_CHECK, report["failures"])
        pins = next(s for s in report["steps"] if s["id"] == STEP_PINS_CHECK)
        self.assertFalse(pins["ok"])
        self.assertIn("pins check failed", pins["error"])


if __name__ == "__main__":
    unittest.main()
