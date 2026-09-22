"""Probe-tree CLI + smoke step (mutate-axis power / wrong-axis negative controls)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _env() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)
    return env


class TestProbeTreeReport(unittest.TestCase):
    def test_build_report_full8_ok(self):
        from diptych import OPERATORS
        from diptych.probe_tree import build_probe_tree_report

        report = build_probe_tree_report()
        self.assertTrue(report["ok"], report.get("failures"))
        self.assertEqual(report["failures"], [])
        self.assertEqual(report["operator_count"], len(OPERATORS))
        for op in OPERATORS:
            cell = report["operators"][op]
            self.assertTrue(cell["ok"], cell)
            self.assertTrue(cell["mutate_axis"]["power_ok"], op)
            self.assertFalse(cell["wrong_axis"]["falsely_flipped"], op)
            am = cell["amortization"]
            self.assertIn("nodes_with_prefix_share", am)
            self.assertIn("nodes_saved_by_prefix_share", am)
            # Structural counters only — no money/timing keys.
            blob = json.dumps(am).lower()
            self.assertNotIn("dollar", blob)
            self.assertNotIn("timing", blob)
            self.assertNotIn("wall_clock", blob)

    def test_cli_json_exit_zero(self):
        proc = subprocess.run(
            [sys.executable, "-m", "diptych.probe_tree", "--json"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=False,
            env=_env(),
        )
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        report = json.loads(proc.stdout)
        self.assertTrue(report["ok"])
        self.assertEqual(report["operator_count"], 8)

    def test_cli_human_ok(self):
        proc = subprocess.run(
            [sys.executable, "-m", "diptych.probe_tree"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=False,
            env=_env(),
        )
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        self.assertIn("PROBE-TREE OK", proc.stdout)
        self.assertIn("mutate_axis.power_ok=True", proc.stdout)
        self.assertIn("wrong_axis.falsely_flipped=False", proc.stdout)


class TestSmokeProbeTreeStep(unittest.TestCase):
    def test_smoke_includes_probe_tree_step(self):
        from diptych.smoke import SMOKE_SCHEMA, SMOKE_STEP_IDS, run_smoke

        self.assertEqual(SMOKE_SCHEMA, "1.9")
        self.assertIn("probe_tree", SMOKE_STEP_IDS)
        report = run_smoke()
        self.assertTrue(report["ok"], report.get("failures"))
        self.assertEqual(report["smoke_schema"], "1.9")
        ids = [s["id"] for s in report["steps"]]
        self.assertEqual(ids, list(SMOKE_STEP_IDS))
        by_id = {s["id"]: s for s in report["steps"]}
        pt = by_id["probe_tree"]
        self.assertTrue(pt["ok"], pt)
        detail = pt["detail"]
        self.assertEqual(detail["operator_count"], 8)
        self.assertEqual(detail["failure_count"], 0)
        for op, cell in detail["operators"].items():
            self.assertTrue(cell["mutate_axis_power_ok"], op)
            self.assertFalse(cell["wrong_axis_falsely_flipped"], op)
        blob = json.dumps(report).lower()
        self.assertNotIn('"auroc"', blob)
        self.assertNotIn('"lab_auroc"', blob)


if __name__ == "__main__":
    unittest.main()
