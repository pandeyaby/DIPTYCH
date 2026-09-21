"""RQ2 ablation harness: operator leave-one-out on cassette controls."""

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


class TestAblationHappyPath(unittest.TestCase):
    def test_build_report_full8_loo_ok(self):
        from diptych import OPERATORS
        from diptych.ablation import build_ablation_report
        from diptych.coupling import CANONICAL_COUPLING

        report = build_ablation_report()
        self.assertTrue(report["ok"], report.get("failures"))
        self.assertEqual(report["failures"], [])
        self.assertEqual(report["operator_count"], len(OPERATORS))
        full8 = report["full8"]
        self.assertTrue(full8["ok"])
        self.assertEqual(full8["hyper_separates_count"], len(OPERATORS))
        self.assertEqual(full8["control_separation_index"], 1.0)
        for op in OPERATORS:
            cell = report["ablations"][op]
            self.assertEqual(cell["held_out"], op)
            self.assertEqual(cell["coupling"], CANONICAL_COUPLING[op])
            self.assertEqual(cell["remaining_operator_count"], len(OPERATORS) - 1)
            self.assertTrue(cell["remaining_bank_separates"], op)
            self.assertTrue(cell["full8_own_hyper_separates"], op)
            self.assertTrue(cell["loses_own_separation"], op)
            # Structural flags on controls: each op is necessary for its own
            # contrast; peers still separate without it.
            self.assertTrue(cell["marginal_necessary"], op)
            self.assertTrue(cell["redundancy_with_peers"], op)
            self.assertEqual(
                set(cell["remaining_operators"]),
                set(OPERATORS) - {op},
            )
        # Coupling strata rollup present (structural only).
        self.assertIn("open_loop", report["coupling_strata"])
        self.assertIn("crn_closed_loop", report["coupling_strata"])
        self.assertEqual(
            report["coupling_strata"]["crn_closed_loop"]["operator_count"],
            2,
        )
        self.assertIn("NOT AUROC", report["non_claims"])
        blob = json.dumps(report).lower()
        self.assertNotIn('"auroc"', blob)
        self.assertNotIn('"lab_auroc"', blob)
        self.assertNotIn('"model_score"', blob)

    def test_cli_json_exit_zero(self):
        proc = subprocess.run(
            [sys.executable, "-m", "diptych.ablation", "--json"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=False,
            env=_env(),
        )
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        report = json.loads(proc.stdout)
        self.assertTrue(report["ok"])
        self.assertEqual(report["full8"]["control_separation_index"], 1.0)
        self.assertEqual(report["operator_count"], 8)
        self.assertEqual(len(report["ablations"]), 8)

    def test_cli_human_ok(self):
        proc = subprocess.run(
            [sys.executable, "-m", "diptych.ablation"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=False,
            env=_env(),
        )
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        self.assertIn("ABLATION OK", proc.stdout)
        self.assertIn("marginal_necessary=", proc.stdout)
        self.assertIn("redundancy_with_peers=", proc.stdout)
        lower = proc.stdout.lower()
        self.assertNotIn("auroc=", lower)
        self.assertNotIn("model score=", lower)


class TestAblationFailureCases(unittest.TestCase):
    def test_full8_failure_exits_nonzero(self):
        from diptych.ablation import build_ablation_report, main

        def always_pass(doc: dict[str, Any]) -> dict[str, Any]:
            return {
                "actual_verdict": "pass",
                "reason": "test double: always pass",
            }

        report = build_ablation_report(hyper_grader=always_pass)
        self.assertFalse(report["ok"])
        self.assertFalse(report["full8"]["ok"])
        self.assertLess(report["full8"]["control_separation_index"], 1.0)
        self.assertTrue(
            any("hyper_separates" in f for f in report["failures"]),
            report["failures"],
        )
        # Broken cassette: violating == conforming → full-8 fails.
        with tempfile.TemporaryDirectory() as tmp:
            broken = Path(tmp) / "cassette"
            shutil.copytree(CASSETTE, broken)
            src = broken / "RESEED" / "conforming" / "probe.json"
            dst = broken / "RESEED" / "violating" / "probe.json"
            doc = json.loads(src.read_text(encoding="utf-8"))
            doc["control_role"] = "violating"
            doc["expected_verdict"] = "fail"
            doc["probe_id"] = "cassette.reseed.violating.broken"
            dst.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
            rc = main(["--cassette", str(broken), "--json"])
            self.assertNotEqual(rc, 0)

    def test_loo_flags_reflect_held_out_loss(self):
        from diptych import OPERATORS
        from diptych.ablation import build_ablation_report

        # Real full-8 is green; holding out any op still leaves peers green.
        report = build_ablation_report()
        for op in OPERATORS:
            cell = report["ablations"][op]
            self.assertTrue(cell["loses_own_separation"])
            self.assertTrue(cell["marginal_necessary"])
            # Remaining bank must not include the held-out op.
            self.assertNotIn(op, cell["remaining_operators"])


class TestSmokeAblationStep(unittest.TestCase):
    def test_smoke_includes_ablation_step(self):
        from diptych.smoke import SMOKE_SCHEMA, SMOKE_STEP_IDS, run_smoke

        self.assertEqual(SMOKE_SCHEMA, "1.6")
        self.assertIn("ablation", SMOKE_STEP_IDS)
        report = run_smoke()
        self.assertTrue(report["ok"], report.get("failures"))
        self.assertEqual(report["smoke_schema"], "1.6")
        ids = [s["id"] for s in report["steps"]]
        self.assertEqual(ids, list(SMOKE_STEP_IDS))
        by_id = {s["id"]: s for s in report["steps"]}
        abl = by_id["ablation"]
        self.assertTrue(abl["ok"], abl)
        detail = abl["detail"]
        self.assertEqual(detail["operator_count"], 8)
        self.assertTrue(detail["full8_ok"])
        self.assertEqual(detail["full8_hyper_separates_count"], 8)
        self.assertEqual(detail["control_separation_index"], 1.0)
        for op, cell in detail["ablations"].items():
            self.assertTrue(cell["marginal_necessary"], op)
            self.assertTrue(cell["redundancy_with_peers"], op)
            self.assertTrue(cell["remaining_bank_separates"], op)
        blob = json.dumps(report).lower()
        self.assertNotIn('"auroc"', blob)
        self.assertNotIn('"lab_auroc"', blob)


if __name__ == "__main__":
    unittest.main()
