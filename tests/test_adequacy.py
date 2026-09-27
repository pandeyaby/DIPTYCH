"""Grader adequacy sweep + regression tests for defects it surfaced."""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CASSETTE = ROOT / "examples" / "fixtures" / "cassette"


def _load(op: str, role: str = "conforming") -> dict:
    return json.loads((CASSETTE / op / role / "probe.json").read_text(encoding="utf-8"))


def _verdict(doc: dict) -> str:
    from diptych.grade import grade_document

    return grade_document(doc).actual_verdict


class TestAdequacyReport(unittest.TestCase):
    def test_report_ok_and_counts_consistent(self):
        from diptych import OPERATORS
        from diptych.adequacy import build_adequacy_report

        report = build_adequacy_report()
        self.assertTrue(report["ok"])
        self.assertEqual(report["base_not_pass"], [])
        self.assertEqual(set(report["by_operator"]), set(OPERATORS))
        agg = report["aggregate"]
        self.assertGreater(agg["n_on_axis"], 0)
        self.assertEqual(
            agg["n_on_axis"],
            sum(r["n_on_axis"] for r in report["by_operator"].values()),
        )
        self.assertLessEqual(agg["n_on_axis_killed"], agg["n_on_axis"])
        self.assertNotIn("auroc", json.dumps(report).lower().replace("not auroc", ""))

    def test_every_operator_has_on_axis_mutants(self):
        from diptych.adequacy import build_adequacy_report

        for op, row in build_adequacy_report()["by_operator"].items():
            self.assertGreater(row["n_on_axis"], 0, op)

    def test_cli_json(self):
        env = dict(os.environ, PYTHONPATH=str(ROOT))
        out = subprocess.run(
            [sys.executable, "-m", "diptych.adequacy", "--json"],
            cwd=ROOT, env=env, capture_output=True, text=True, check=False,
        )
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertTrue(json.loads(out.stdout)["ok"])


class TestGraderRegressions(unittest.TestCase):
    """Each case was a surviving on-axis mutant before the grader fix."""

    def test_signflip_fingerprint_does_not_bypass_data_check(self):
        doc = _load("SIGNFLIP")
        self.assertEqual(_verdict(doc), "pass")
        bad = copy.deepcopy(doc)
        bad["traces"][1]["channels"]["signal"]["values"][0] += 3.0
        fp0 = bad["traces"][0]["meta"].get("sign_normalized_fingerprint")
        self.assertEqual(fp0, bad["traces"][1]["meta"].get("sign_normalized_fingerprint"))
        self.assertEqual(_verdict(bad), "fail")

    def test_trajswap_negative_residual_beyond_bound_fails(self):
        doc = _load("TRAJSWAP")
        bad = copy.deepcopy(doc)
        bound = bad["traces"][0]["meta"]["residual_bound"]
        bad["traces"][0]["channels"]["closed_loop_residual"]["values"][3] = -(bound + 1.0)
        self.assertEqual(_verdict(bad), "fail")

    def test_twin_parameter_mismatch_fails(self):
        cases = [
            ("RESEED", "epsilon", 10.0),
            ("SATEXTEND", "sat_hi", 50.0),
            ("TRAJSWAP", "residual_bound", 500.0),
            ("VARSCALE", "var_scale_bound", 500.0),
        ]
        for op, key, value in cases:
            with self.subTest(op=op, key=key):
                doc = _load(op)
                self.assertEqual(_verdict(doc), "pass")
                bad = copy.deepcopy(doc)
                bad["traces"][1]["meta"][key] = value
                self.assertEqual(_verdict(bad), "fail")

    def test_one_sided_parameter_still_used(self):
        doc = _load("RESEED")
        one = copy.deepcopy(doc)
        one["traces"][1]["meta"].pop("epsilon", None)
        self.assertEqual(_verdict(one), "pass")


if __name__ == "__main__":
    unittest.main()
