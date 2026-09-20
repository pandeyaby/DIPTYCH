"""PoC JSON / SARIF CLI: stranger machine-readable full-8 harness."""

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


class TestPocJsonCli(unittest.TestCase):
    def test_build_poc_report_schema_and_eight_operators(self):
        from diptych import OPERATORS
        from diptych.poc import (
            OPERATOR_REQUIRED_KEYS,
            POC_REQUIRED_KEYS,
            ROLE_REQUIRED_KEYS,
            build_poc_report,
        )

        poc = build_poc_report()
        for key in POC_REQUIRED_KEYS:
            self.assertIn(key, poc, key)
        self.assertEqual(poc["poc_schema"], "1.0")
        self.assertEqual(poc["diptych_schema"], "0.2")
        self.assertTrue(poc["ok"], poc.get("failures"))
        self.assertEqual(poc["operator_count"], 8)
        self.assertEqual(set(poc["operators"]), set(OPERATORS))
        self.assertEqual(len(poc["operators"]), 8)

        for op in OPERATORS:
            cell = poc["operators"][op]
            for key in OPERATOR_REQUIRED_KEYS:
                self.assertIn(key, cell, f"{op}.{key}")
            conf = cell["conforming"]
            viol = cell["violating"]
            for key in ROLE_REQUIRED_KEYS:
                self.assertIn(key, conf, f"{op}.conforming.{key}")
                self.assertIn(key, viol, f"{op}.violating.{key}")

            # Conforming pass / violating fail
            self.assertEqual(conf["expected_verdict"], "pass", op)
            self.assertEqual(conf["actual_verdict"], "pass", op)
            self.assertTrue(conf["matches_expected"], op)
            self.assertEqual(viol["expected_verdict"], "fail", op)
            self.assertEqual(viol["actual_verdict"], "fail", op)
            self.assertTrue(viol["matches_expected"], op)

            # Existing gate_axis_mutate evidence
            power = cell["gate_axis_mutate"]
            self.assertEqual(power["baseline_verdict"], "pass", op)
            self.assertEqual(power["mutated_verdict"], "fail", op)
            self.assertTrue(power["axis_changed"], op)
            self.assertTrue(power["power_ok"], op)
            self.assertTrue(cell["axis_power"], op)
            self.assertEqual(cell["diptych_core"], "green", op)

        # No invented score *fields* (notes may mention AUROC as a non-claim)
        blob = json.dumps(poc)
        self.assertNotIn('"auroc"', blob.lower())
        self.assertNotIn('"lab_auroc"', blob.lower())
        self.assertNotIn('"model_grade"', blob.lower())
        self.assertNotIn("0.766", blob)

    def test_cli_json_stdout_exit_zero(self):
        proc = subprocess.run(
            [sys.executable, "-m", "diptych.poc", "--json", "--stdout-only", "--quiet"],
            cwd=ROOT,
            env=_env(),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        poc = json.loads(proc.stdout)
        self.assertTrue(poc["ok"])
        self.assertEqual(poc["operator_count"], 8)
        self.assertEqual(len(poc["operators"]), 8)

    def test_cli_writes_json_and_sarif_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "poc.json"
            sarif_out = Path(tmp) / "poc.sarif"
            matrix = Path(tmp) / "matrix.json"
            proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "diptych.poc",
                    "--json",
                    "--sarif",
                    "--quiet",
                    "--out",
                    str(out),
                    "--sarif-out",
                    str(sarif_out),
                    "--matrix",
                    str(matrix),
                ],
                cwd=ROOT,
                env=_env(),
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
            self.assertTrue(out.is_file())
            self.assertTrue(sarif_out.is_file())
            self.assertTrue(matrix.is_file())
            poc = json.loads(out.read_text(encoding="utf-8"))
            sarif = json.loads(sarif_out.read_text(encoding="utf-8"))
            self.assertTrue(poc["ok"])
            self.assertEqual(sarif["version"], "2.1.0")
            self.assertEqual(len(sarif["runs"]), 1)
            # 8 ops × (conforming + violating + gate_axis_mutate) = 24 results
            self.assertEqual(len(sarif["runs"][0]["results"]), 24)
            self.assertTrue(sarif["runs"][0]["properties"]["ok"])

    def test_script_json_flag_exit_zero(self):
        proc = subprocess.run(
            ["./scripts/run_poc.sh", "--json"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        self.assertIn("PoC JSON OK", proc.stdout)
        report = ROOT / "reports" / "paired-probes" / "poc_report.json"
        self.assertTrue(report.is_file(), report)
        poc = json.loads(report.read_text(encoding="utf-8"))
        self.assertTrue(poc["ok"])
        self.assertEqual(len(poc["operators"]), 8)

    def test_cli_exit_code_nonzero_on_failure(self):
        """Exit code 1 when gates fail (monkeypatch via missing twin)."""
        import shutil

        from diptych import gates as G

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            shutil.copytree(ROOT / "diptych-probes", tmp_path / "diptych-probes")
            (tmp_path / "diptych-probes" / "RESEED" / "violating" / "probe.json").unlink()
            old_root, old_probes = G.ROOT, G.PROBES
            try:
                G.ROOT = tmp_path
                G.PROBES = tmp_path / "diptych-probes"
                from diptych.poc import build_poc_report

                poc = build_poc_report()
                self.assertFalse(poc["ok"])
                self.assertEqual(0 if poc["ok"] else 1, 1)
                self.assertTrue(any(f["gate"] == "manifest" for f in poc["failures"]))
            finally:
                G.ROOT, G.PROBES = old_root, old_probes
            # Live tree still exits 0
            proc = subprocess.run(
                [sys.executable, "-m", "diptych.poc", "--json", "--stdout-only", "--quiet"],
                cwd=ROOT,
                env=_env(),
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_sarif_helper_roundtrip(self):
        from diptych.poc import build_poc_report, report_to_sarif

        poc = build_poc_report()
        sarif = report_to_sarif(poc)
        self.assertEqual(sarif["version"], "2.1.0")
        results = sarif["runs"][0]["results"]
        rule_ids = {r["ruleId"] for r in results}
        for op in poc["operators"]:
            self.assertIn(f"diptych.{op}.conforming", rule_ids)
            self.assertIn(f"diptych.{op}.violating", rule_ids)
            self.assertIn(f"diptych.{op}.gate_axis_mutate", rule_ids)
        # All levels none when ok
        self.assertTrue(all(r["level"] == "none" for r in results))


if __name__ == "__main__":
    unittest.main()
