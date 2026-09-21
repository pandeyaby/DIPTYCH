"""RQ1 separation harness: single-trace baseline vs hyperproperty on cassette."""

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


class TestSeparationHappyPath(unittest.TestCase):
    def test_build_report_full8_ok(self):
        from diptych import OPERATORS
        from diptych.separation import build_separation_report

        report = build_separation_report()
        self.assertTrue(report["ok"], report.get("failures"))
        self.assertEqual(report["failures"], [])
        self.assertEqual(report["operator_count"], len(OPERATORS))
        self.assertEqual(report["separates_count"], len(OPERATORS))
        self.assertEqual(report["control_separation_index"], 1.0)
        for op in OPERATORS:
            cell = report["operators"][op]
            self.assertTrue(cell["single_trace_equiv"], op)
            self.assertTrue(cell["hyper_separates"], op)
            self.assertTrue(cell["separates"], op)
            self.assertEqual(cell["hyperproperty"]["conforming_verdict"], "pass", op)
            self.assertEqual(cell["hyperproperty"]["violating_verdict"], "fail", op)
            self.assertEqual(
                cell["single_trace"]["conforming_verdict"],
                cell["single_trace"]["violating_verdict"],
                op,
            )
        blob = json.dumps(report).lower()
        self.assertNotIn('"auroc"', blob)
        self.assertNotIn('"lab_auroc"', blob)
        self.assertNotIn('"model_score"', blob)

    def test_cli_json_exit_zero(self):
        proc = subprocess.run(
            [sys.executable, "-m", "diptych.separation", "--json"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=False,
            env=_env(),
        )
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        report = json.loads(proc.stdout)
        self.assertTrue(report["ok"])
        self.assertEqual(report["control_separation_index"], 1.0)
        self.assertEqual(report["operator_count"], 8)

    def test_cli_human_ok(self):
        proc = subprocess.run(
            [sys.executable, "-m", "diptych.separation"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=False,
            env=_env(),
        )
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        self.assertIn("SEPARATION OK", proc.stdout)
        self.assertIn("control_separation_index=1.0", proc.stdout)
        lower = proc.stdout.lower()
        self.assertNotIn("auroc=", lower)
        self.assertNotIn("model score=", lower)

    def test_single_trace_baseline_ignores_other_twin(self):
        from diptych.separation import grade_single_trace_baseline, load_cassette_probe

        conf = load_cassette_probe("RESEED", "conforming")
        viol = load_cassette_probe("RESEED", "violating")
        # Wipe twin[1] on both — baseline must not care.
        conf["traces"][1]["channels"] = {}
        viol["traces"][1]["channels"] = {}
        c = grade_single_trace_baseline(conf, twin_index=0)
        v = grade_single_trace_baseline(viol, twin_index=0)
        self.assertEqual(c["actual_verdict"], "pass")
        self.assertEqual(v["actual_verdict"], "pass")
        self.assertFalse(c["evidence"]["twins_compared"])


class TestSeparationFailureCases(unittest.TestCase):
    def test_hyper_separates_failure_exits_nonzero(self):
        from diptych.separation import build_separation_report, main

        def always_pass(doc: dict[str, Any]) -> dict[str, Any]:
            return {
                "actual_verdict": "pass",
                "reason": "test double: always pass",
            }

        report = build_separation_report(hyper_grader=always_pass)
        self.assertFalse(report["ok"])
        self.assertLess(report["control_separation_index"], 1.0)
        self.assertTrue(any("hyper_separates" in f for f in report["failures"]))
        # CLI against a broken cassette (violating missing) also fails.
        with tempfile.TemporaryDirectory() as tmp:
            broken = Path(tmp) / "cassette"
            shutil.copytree(CASSETTE, broken)
            # Make RESEED violating identical to conforming → hyper should not separate
            # when using real grade_operator: both pass.
            src = broken / "RESEED" / "conforming" / "probe.json"
            dst = broken / "RESEED" / "violating" / "probe.json"
            doc = json.loads(src.read_text(encoding="utf-8"))
            doc["control_role"] = "violating"
            doc["expected_verdict"] = "fail"
            doc["probe_id"] = "cassette.reseed.violating.broken"
            dst.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
            rc = main(["--cassette", str(broken), "--json"])
            self.assertNotEqual(rc, 0)

    def test_single_trace_equiv_failure_drops_index(self):
        from diptych.separation import build_separation_report

        def role_aware_baseline(doc: dict[str, Any], *, twin_index: int = 0) -> dict[str, Any]:
            # Illicitly peeks at control_role — breaks single_trace_equiv by construction.
            role = doc.get("control_role")
            verdict = "pass" if role == "conforming" else "fail"
            return {
                "actual_verdict": verdict,
                "reason": f"test double peeked control_role={role}",
                "evidence": {"twins_compared": False, "twin_index": twin_index},
            }

        report = build_separation_report(single_trace_grader=role_aware_baseline)
        self.assertFalse(report["ok"])
        self.assertEqual(report["control_separation_index"], 0.0)
        self.assertIn("control_separation_index<1.0", report["failures"])
        for op, cell in report["operators"].items():
            self.assertFalse(cell["single_trace_equiv"], op)
            self.assertTrue(cell["hyper_separates"], op)
            self.assertFalse(cell["separates"], op)


class TestSmokeSeparationStep(unittest.TestCase):
    def test_smoke_includes_separation_step(self):
        from diptych.smoke import SMOKE_SCHEMA, SMOKE_STEP_IDS, run_smoke

        self.assertEqual(SMOKE_SCHEMA, "1.7")
        self.assertIn("separation", SMOKE_STEP_IDS)
        report = run_smoke()
        self.assertTrue(report["ok"], report.get("failures"))
        self.assertEqual(report["smoke_schema"], "1.7")
        ids = [s["id"] for s in report["steps"]]
        self.assertEqual(ids, list(SMOKE_STEP_IDS))
        by_id = {s["id"]: s for s in report["steps"]}
        sep = by_id["separation"]
        self.assertTrue(sep["ok"], sep)
        detail = sep["detail"]
        self.assertEqual(detail["operator_count"], 8)
        self.assertEqual(detail["separates_count"], 8)
        self.assertEqual(detail["control_separation_index"], 1.0)
        for op, cell in detail["operators"].items():
            self.assertTrue(cell["single_trace_equiv"], op)
            self.assertTrue(cell["hyper_separates"], op)
            self.assertTrue(cell["separates"], op)
        blob = json.dumps(report).lower()
        self.assertNotIn('"auroc"', blob)
        self.assertNotIn('"lab_auroc"', blob)


if __name__ == "__main__":
    unittest.main()
