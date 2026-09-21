"""RQ4 amortization harness: structural probe-tree counters on cassette controls."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CASSETTE = ROOT / "examples" / "fixtures" / "cassette"


def _env() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)
    return env


class TestAmortizationHappyPath(unittest.TestCase):
    def test_build_report_full8_ok(self):
        from diptych import OPERATORS
        from diptych.amortization import build_amortization_report
        from diptych.probe_tree import prefix_share_amortization

        report = build_amortization_report()
        self.assertTrue(report["ok"], report.get("failures"))
        self.assertEqual(report["failures"], [])
        self.assertEqual(report["operator_count"], len(OPERATORS))
        expected = prefix_share_amortization(8)
        for op in OPERATORS:
            cell = report["operators"][op]
            self.assertEqual(cell["operator"], op)
            self.assertTrue(cell["tree_ok"], op)
            self.assertTrue(cell["counters_consistent"], op)
            self.assertTrue(cell["ok"], op)
            am = cell["amortization"]
            for key, value in expected.items():
                self.assertEqual(am[key], value, f"{op}:{key}")
            self.assertAlmostEqual(cell["alpha"], 10 / 16)
        agg = report["aggregate"]
        self.assertTrue(agg["counters_consistent"])
        self.assertEqual(agg["amortization"]["shared_prefix_nodes"], 8 * len(OPERATORS))
        self.assertEqual(agg["amortization"]["nodes_naive_two_probes"], 16 * len(OPERATORS))
        self.assertEqual(agg["amortization"]["nodes_with_prefix_share"], 10 * len(OPERATORS))
        self.assertEqual(agg["amortization"]["nodes_saved_by_prefix_share"], 6 * len(OPERATORS))
        self.assertAlmostEqual(agg["alpha"], 0.625)
        self.assertIn("NOT AUROC", report["non_claims"])
        self.assertTrue(any("dollar" in c.lower() for c in report["non_claims"]))
        blob = json.dumps(report).lower()
        self.assertNotIn('"auroc"', blob)
        self.assertNotIn('"lab_auroc"', blob)
        self.assertNotIn('"model_score"', blob)
        self.assertNotIn('"usd"', blob)
        self.assertNotIn('"wall_clock"', blob)

    def test_cli_json_exit_zero(self):
        proc = subprocess.run(
            [sys.executable, "-m", "diptych.amortization", "--json"],
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
        self.assertEqual(len(report["operators"]), 8)
        self.assertAlmostEqual(report["aggregate"]["alpha"], 0.625)

    def test_cli_human_ok(self):
        proc = subprocess.run(
            [sys.executable, "-m", "diptych.amortization"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=False,
            env=_env(),
        )
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        self.assertIn("AMORTIZATION OK", proc.stdout)
        self.assertIn("alpha=", proc.stdout)
        lower = proc.stdout.lower()
        self.assertNotIn("auroc=", lower)
        self.assertNotIn("model score=", lower)
        self.assertNotIn("$", proc.stdout)


class TestAmortizationFailureCases(unittest.TestCase):
    def test_inconsistent_counters_fail(self):
        from diptych.amortization import build_amortization_report

        def broken_load(op: str) -> dict[str, Any]:
            # Load a real conforming probe, then the tree path is fine — inject
            # inconsistency via a patched execute by returning a tree-shaped
            # stub through a custom load that still validates. Instead: wrap
            # execute by building report then mutating is not available; use
            # evaluate path via monkeypatch of execute_full8_probe_trees.
            from diptych.probe_tree import load_cassette_conforming

            return load_cassette_conforming(op, CASSETTE)

        # Patch execute_full8_probe_trees to return inconsistent amortization.
        import diptych.amortization as mod
        from diptych import OPERATORS

        def fake_execute(load_conf):  # noqa: ARG001
            trees = {}
            for op in OPERATORS:
                trees[op] = {
                    "operator": op,
                    "ok": True,
                    "amortization": {
                        "shared_prefix_nodes": 8,
                        "branch_suffix_nodes": 2,
                        "nodes_naive_two_probes": 16,
                        "nodes_with_prefix_share": 10,
                        # Inconsistent: should be 6 (= 16-10)
                        "nodes_saved_by_prefix_share": 99,
                    },
                }
            return {"operator_count": len(OPERATORS), "trees": trees, "ok": True}

        original = mod.execute_full8_probe_trees
        mod.execute_full8_probe_trees = fake_execute  # type: ignore[assignment]
        try:
            report = build_amortization_report(load_conf=broken_load)
        finally:
            mod.execute_full8_probe_trees = original  # type: ignore[assignment]

        self.assertFalse(report["ok"])
        self.assertTrue(
            any("amortization_inconsistent" in f for f in report["failures"]),
            report["failures"],
        )

    def test_tree_not_ok_fails(self):
        from diptych import OPERATORS
        from diptych.amortization import build_amortization_report
        import diptych.amortization as mod

        def fake_execute(load_conf):  # noqa: ARG001
            trees = {}
            for i, op in enumerate(OPERATORS):
                trees[op] = {
                    "operator": op,
                    "ok": i != 0,  # first op fails
                    "error": "forced_fail" if i == 0 else None,
                    "amortization": {
                        "shared_prefix_nodes": 8,
                        "branch_suffix_nodes": 2,
                        "nodes_naive_two_probes": 16,
                        "nodes_with_prefix_share": 10,
                        "nodes_saved_by_prefix_share": 6,
                    },
                }
            return {"operator_count": len(OPERATORS), "trees": trees, "ok": False}

        original = mod.execute_full8_probe_trees
        mod.execute_full8_probe_trees = fake_execute  # type: ignore[assignment]
        try:
            report = build_amortization_report()
        finally:
            mod.execute_full8_probe_trees = original  # type: ignore[assignment]

        self.assertFalse(report["ok"])
        self.assertTrue(
            any("tree_not_ok" in f for f in report["failures"]),
            report["failures"],
        )
        from contextlib import redirect_stdout
        from io import StringIO

        buf = StringIO()
        with redirect_stdout(buf):
            # Re-patch for CLI path
            mod.execute_full8_probe_trees = fake_execute  # type: ignore[assignment]
            try:
                from diptych.amortization import main

                rc = main(["--json"])
            finally:
                mod.execute_full8_probe_trees = original  # type: ignore[assignment]
        self.assertNotEqual(rc, 0)
        cli_report = json.loads(buf.getvalue())
        self.assertFalse(cli_report["ok"])


class TestSmokeAmortizationStep(unittest.TestCase):
    def test_smoke_includes_amortization_step(self):
        from diptych.smoke import SMOKE_SCHEMA, SMOKE_STEP_IDS, run_smoke

        self.assertEqual(SMOKE_SCHEMA, "1.8")
        self.assertIn("amortization", SMOKE_STEP_IDS)
        report = run_smoke()
        self.assertTrue(report["ok"], report.get("failures"))
        self.assertEqual(report["smoke_schema"], "1.8")
        ids = [s["id"] for s in report["steps"]]
        self.assertEqual(ids, list(SMOKE_STEP_IDS))
        by_id = {s["id"]: s for s in report["steps"]}
        am = by_id["amortization"]
        self.assertTrue(am["ok"], am)
        detail = am["detail"]
        self.assertEqual(detail["operator_count"], 8)
        self.assertAlmostEqual(detail["alpha"], 0.625)
        self.assertTrue(detail["aggregate"]["counters_consistent"])
        self.assertEqual(detail["aggregate"]["amortization"]["nodes_naive_two_probes"], 128)
        self.assertEqual(detail["aggregate"]["amortization"]["nodes_with_prefix_share"], 80)
        self.assertEqual(detail["aggregate"]["amortization"]["nodes_saved_by_prefix_share"], 48)
        for op, cell in detail["operators"].items():
            self.assertTrue(cell["ok"], op)
            self.assertTrue(cell["tree_ok"], op)
            self.assertTrue(cell["counters_consistent"], op)
        self.assertIn("NOT AUROC", detail["non_claims"])
        blob = json.dumps(report).lower()
        self.assertNotIn('"auroc"', blob)
        self.assertNotIn('"lab_auroc"', blob)
        self.assertNotIn('"usd"', blob)


if __name__ == "__main__":
    unittest.main()
