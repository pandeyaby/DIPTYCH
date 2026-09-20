"""Grade CLI SARIF 2.1.0 export (adapter consumers; no invented scores)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "examples" / "fixtures"
THIN = ROOT / "tests" / "fixtures" / "thin"
CASSETTE_INCONCLUSIVE = (
    FIXTURES / "cassette" / "RESEED" / "inconclusive_horizon" / "probe.json"
)


def _cli_env() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)
    return env


class TestGradeSarif(unittest.TestCase):
    def test_report_to_sarif_shape_pass_fail(self):
        from diptych.grade import build_grade_report, report_to_sarif

        report = build_grade_report(FIXTURES / "zeroday" / "RESEED")
        sarif = report_to_sarif(report)
        self.assertEqual(sarif["version"], "2.1.0")
        self.assertEqual(
            sarif["$schema"], "https://json.schemastore.org/sarif-2.1.0.json"
        )
        self.assertEqual(len(sarif["runs"]), 1)
        run = sarif["runs"][0]
        self.assertEqual(run["tool"]["driver"]["name"], "diptych.grade")
        rule_ids = {r["id"] for r in run["tool"]["driver"]["rules"]}
        self.assertEqual(
            rule_ids,
            {
                "diptych.grade.pass",
                "diptych.grade.fail",
                "diptych.grade.inconclusive",
                "diptych.grade.rejected",
            },
        )
        self.assertTrue(run["properties"]["ok"])
        self.assertEqual(len(run["results"]), 2)
        by_role = {
            r["properties"]["control_role"]: r for r in run["results"]
        }
        conf = by_role["conforming"]
        viol = by_role["violating"]
        self.assertEqual(conf["ruleId"], "diptych.grade.pass")
        self.assertEqual(conf["level"], "none")
        self.assertEqual(conf["properties"]["actual_verdict"], "pass")
        self.assertEqual(viol["ruleId"], "diptych.grade.fail")
        self.assertEqual(viol["level"], "error")
        self.assertEqual(viol["properties"]["actual_verdict"], "fail")
        # No invented score keys in SARIF properties.
        for r in run["results"]:
            props = r["properties"]
            for banned in ("auroc", "AUROC", "lab_auroc", "model_grade", "f1"):
                self.assertNotIn(banned, props)

    def test_sarif_inconclusive_warning(self):
        from diptych.grade import build_grade_report, report_to_sarif

        report = build_grade_report(CASSETTE_INCONCLUSIVE)
        sarif = report_to_sarif(report)
        result = sarif["runs"][0]["results"][0]
        self.assertEqual(result["ruleId"], "diptych.grade.inconclusive")
        self.assertEqual(result["level"], "warning")
        self.assertEqual(result["properties"]["actual_verdict"], "inconclusive")

    def test_sarif_rejected_thin(self):
        from diptych.grade import build_grade_report, report_to_sarif

        report = build_grade_report(THIN / "auroc_field.json")
        sarif = report_to_sarif(report)
        result = sarif["runs"][0]["results"][0]
        self.assertEqual(result["ruleId"], "diptych.grade.rejected")
        self.assertEqual(result["level"], "error")
        self.assertIn("forbidden score", result["properties"]["error"])

    def test_cli_sarif_flag(self):
        path = FIXTURES / "aomb" / "SIGNFLIP" / "conforming" / "probe.json"
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "diptych.grade",
                "--input",
                str(path),
                "--sarif",
            ],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=False,
            env=_cli_env(),
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        sarif = json.loads(proc.stdout)
        self.assertEqual(sarif["version"], "2.1.0")
        self.assertEqual(sarif["runs"][0]["results"][0]["ruleId"], "diptych.grade.pass")
        self.assertEqual(sarif["runs"][0]["results"][0]["level"], "none")

    def test_cli_format_sarif_alias(self):
        path = FIXTURES / "zeroday" / "RESEED" / "violating" / "probe.json"
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "diptych.grade",
                "--input",
                str(path),
                "--format",
                "sarif",
            ],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=False,
            env=_cli_env(),
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        sarif = json.loads(proc.stdout)
        self.assertEqual(sarif["runs"][0]["results"][0]["ruleId"], "diptych.grade.fail")
        self.assertEqual(sarif["runs"][0]["results"][0]["level"], "error")

    def test_cli_json_default_unchanged(self):
        path = FIXTURES / "aomb" / "SIGNFLIP" / "conforming" / "probe.json"
        proc = subprocess.run(
            [sys.executable, "-m", "diptych.grade", "--input", str(path)],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=False,
            env=_cli_env(),
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        report = json.loads(proc.stdout)
        self.assertEqual(report["grade_schema"], "1.0")
        self.assertIn("results", report)
        self.assertNotIn("version", report)  # not SARIF

    def test_cli_sarif_write_output(self):
        path = FIXTURES / "aomb" / "SIGNFLIP" / "conforming" / "probe.json"
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "grade.sarif"
            proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "diptych.grade",
                    "--input",
                    str(path),
                    "--sarif",
                    "-o",
                    str(out),
                ],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                check=False,
                env=_cli_env(),
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertTrue(out.is_file())
            sarif = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(sarif["version"], "2.1.0")
            self.assertEqual(len(sarif["runs"][0]["results"]), 1)


if __name__ == "__main__":
    unittest.main()
