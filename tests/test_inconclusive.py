"""RQ5 inconclusive harness: structural rates on cassette fixtures."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CASSETTE = ROOT / "examples" / "fixtures" / "cassette"


def _env() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)
    return env


class TestInconclusiveHappyPath(unittest.TestCase):
    def test_build_report_full8_ok(self):
        from diptych import OPERATORS
        from diptych.inconclusive import build_inconclusive_report

        report = build_inconclusive_report()
        self.assertTrue(report["ok"], report.get("failures"))
        self.assertEqual(report["failures"], [])
        self.assertEqual(report["operator_count"], len(OPERATORS))
        self.assertEqual(report["ops_with_working_inconclusive"], len(OPERATORS))
        self.assertEqual(report["n_probes"], 24)
        self.assertEqual(report["n_inconclusive"], 8)
        self.assertAlmostEqual(report["inconclusive_rate"], 8 / 24)
        self.assertIn("horizon_prefix", report["by_reason_needle"])
        self.assertIn("missing_twin", report["by_reason_needle"])
        self.assertIn("crn_stream_id", report["by_reason_needle"])
        for op in OPERATORS:
            cell = report["operators"][op]
            self.assertEqual(cell["operator"], op)
            self.assertGreaterEqual(cell["n_probes"], 3, op)
            self.assertGreaterEqual(cell["n_inconclusive"], 1, op)
            self.assertTrue(cell["has_working_inconclusive"], op)
            self.assertEqual(cell["inconclusive_mismatches"], [], op)
            self.assertGreater(cell["inconclusive_rate"], 0.0, op)
        self.assertIn("NOT AUROC", report["non_claims"])
        blob = json.dumps(report).lower()
        self.assertNotIn('"auroc"', blob)
        self.assertNotIn('"lab_auroc"', blob)
        self.assertNotIn('"model_score"', blob)

    def test_cli_json_exit_zero(self):
        proc = subprocess.run(
            [sys.executable, "-m", "diptych.inconclusive", "--json"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=False,
            env=_env(),
        )
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        report = json.loads(proc.stdout)
        self.assertTrue(report["ok"])
        self.assertEqual(report["ops_with_working_inconclusive"], 8)
        self.assertEqual(report["n_inconclusive"], 8)
        self.assertEqual(len(report["operators"]), 8)

    def test_cli_human_ok(self):
        proc = subprocess.run(
            [sys.executable, "-m", "diptych.inconclusive"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=False,
            env=_env(),
        )
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        self.assertIn("INCONCLUSIVE OK", proc.stdout)
        self.assertIn("inconclusive_rate=", proc.stdout)
        self.assertIn("by_reason_needle=", proc.stdout)
        lower = proc.stdout.lower()
        self.assertNotIn("auroc=", lower)
        self.assertNotIn("model score=", lower)


class TestInconclusiveFailureCases(unittest.TestCase):
    def test_missing_inconclusive_fixture_fails(self):
        from diptych.inconclusive import build_inconclusive_report, main

        with tempfile.TemporaryDirectory() as tmp:
            broken = Path(tmp) / "cassette"
            shutil.copytree(CASSETTE, broken)
            # Remove RESEED's inconclusive fixture → full-8 coverage fails.
            shutil.rmtree(broken / "RESEED" / "inconclusive_horizon")
            report = build_inconclusive_report(cassette=broken)
            self.assertFalse(report["ok"])
            self.assertTrue(
                any("RESEED" in f and "missing_working_inconclusive" in f for f in report["failures"]),
                report["failures"],
            )
            self.assertIn("full8_inconclusive_coverage<8", report["failures"])
            from contextlib import redirect_stdout
            from io import StringIO

            buf = StringIO()
            with redirect_stdout(buf):
                rc = main(["--cassette", str(broken), "--json"])
            self.assertNotEqual(rc, 0)
            cli_report = json.loads(buf.getvalue())
            self.assertFalse(cli_report["ok"])
            self.assertTrue(cli_report["failures"])

    def test_inconclusive_grade_mismatch_fails(self):
        from diptych.inconclusive import build_inconclusive_report

        def always_pass(doc: dict[str, Any]) -> dict[str, Any]:
            return {
                "actual_verdict": "pass",
                "expected_verdict": doc.get("expected_verdict"),
                "reason": "test double: always pass",
                "matches_expected": doc.get("expected_verdict") == "pass",
            }

        report = build_inconclusive_report(grader=always_pass)
        self.assertFalse(report["ok"])
        self.assertTrue(
            any("inconclusive_mismatch" in f for f in report["failures"]),
            report["failures"],
        )
        self.assertTrue(
            any("missing_working_inconclusive" in f for f in report["failures"]),
            report["failures"],
        )


class TestSmokeInconclusiveStep(unittest.TestCase):
    def test_smoke_protocol_covers_rq5_inconclusive(self):
        from diptych.smoke import SMOKE_SCHEMA, SMOKE_STEP_IDS, run_smoke

        self.assertEqual(SMOKE_SCHEMA, "1.9")
        self.assertIn("protocol", SMOKE_STEP_IDS)
        report = run_smoke()
        self.assertTrue(report["ok"], report.get("failures"))
        self.assertEqual(report["smoke_schema"], "1.9")
        by_id = {s["id"]: s for s in report["steps"]}
        proto = by_id["protocol"]
        self.assertTrue(proto["ok"], proto)
        rq5 = proto["detail"]["rqs"]["RQ5"]
        self.assertTrue(rq5["ok"], rq5)
        self.assertEqual(rq5["harness"], "inconclusive")
        self.assertEqual(rq5["metrics"]["operator_count"], 8)
        self.assertEqual(rq5["metrics"]["ops_with_working_inconclusive"], 8)
        self.assertEqual(rq5["metrics"]["n_inconclusive"], 8)
        self.assertEqual(rq5["metrics"]["n_probes"], 24)
        self.assertAlmostEqual(rq5["metrics"]["inconclusive_rate"], 8 / 24)
        self.assertIn("NOT AUROC", rq5.get("non_claims") or proto["detail"]["non_claims"])
        blob = json.dumps(report).lower()
        self.assertNotIn('"auroc"', blob)
        self.assertNotIn('"lab_auroc"', blob)


if __name__ == "__main__":
    unittest.main()
