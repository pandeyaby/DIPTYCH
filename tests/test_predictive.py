"""RQ3 predictive harness: N/A default + bad held-out reject."""

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


class TestPredictiveDefaultNA(unittest.TestCase):
    def test_build_report_protocol_only_na(self):
        from diptych.predictive import NA, build_predictive_report

        report = build_predictive_report()
        self.assertTrue(report["ok"], report.get("failures"))
        self.assertEqual(report["status"], "protocol_only")
        self.assertIsNone(report["held_out"])
        self.assertEqual(report["n_pairs"], 0)
        self.assertEqual(report["failures"], [])
        results = report["results"]
        self.assertEqual(results["probe_held_out_rank_corr"], NA)
        self.assertEqual(results["trace_only_rank_corr"], NA)
        self.assertEqual(results["probe_beats_trace_baseline"], NA)
        self.assertEqual(results["n_pairs_usable"], 0)
        self.assertEqual(report["protocol"]["baseline"], "trace-only")
        self.assertIn("NOT AUROC", report["non_claims"])
        self.assertTrue(
            any("model rank" in c.lower() for c in report["non_claims"]),
            report["non_claims"],
        )
        self.assertTrue(
            any("empirical rq3" in c.lower() for c in report["non_claims"]),
            report["non_claims"],
        )
        blob = json.dumps(report).lower()
        self.assertNotIn('"auroc"', blob)
        self.assertNotIn('"lab_auroc"', blob)
        self.assertNotIn('"model_rank"', blob)
        self.assertNotIn('"model_score"', blob)

    def test_cli_json_exit_zero_na(self):
        proc = subprocess.run(
            [sys.executable, "-m", "diptych.predictive", "--json"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=False,
            env=_env(),
        )
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        report = json.loads(proc.stdout)
        self.assertTrue(report["ok"])
        self.assertEqual(report["status"], "protocol_only")
        self.assertEqual(report["results"]["probe_held_out_rank_corr"], "N/A")
        self.assertEqual(report["results"]["trace_only_rank_corr"], "N/A")

    def test_cli_human_ok(self):
        proc = subprocess.run(
            [sys.executable, "-m", "diptych.predictive"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=False,
            env=_env(),
        )
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        self.assertIn("PREDICTIVE OK", proc.stdout)
        self.assertIn("protocol_only", proc.stdout)
        self.assertIn("N/A", proc.stdout)
        lower = proc.stdout.lower()
        self.assertNotIn("auroc=", lower)
        self.assertNotIn("model rank=", lower)


class TestPredictiveBadHeldOut(unittest.TestCase):
    def test_missing_held_out_fails(self):
        from diptych.predictive import build_predictive_report, main

        missing = Path(tempfile.mkdtemp()) / "no_such_held_out.json"
        report = build_predictive_report(held_out=missing)
        self.assertFalse(report["ok"])
        self.assertEqual(report["status"], "held_out_invalid")
        self.assertTrue(
            any("held_out_missing" in f for f in report["failures"]),
            report["failures"],
        )
        self.assertEqual(report["results"]["probe_held_out_rank_corr"], "N/A")
        from contextlib import redirect_stdout
        from io import StringIO

        buf = StringIO()
        with redirect_stdout(buf):
            rc = main(["--held-out", str(missing), "--json"])
        self.assertNotEqual(rc, 0)
        self.assertFalse(json.loads(buf.getvalue())["ok"])

    def test_malformed_held_out_fails(self):
        from diptych.predictive import build_predictive_report, main

        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad.json"
            bad.write_text("{", encoding="utf-8")
            report = build_predictive_report(held_out=bad)
            self.assertFalse(report["ok"])
            self.assertEqual(report["status"], "held_out_invalid")
            self.assertTrue(report["failures"], report["failures"])
            self.assertEqual(report["results"]["probe_held_out_rank_corr"], "N/A")
            from contextlib import redirect_stdout
            from io import StringIO

            buf = StringIO()
            with redirect_stdout(buf):
                rc = main(["--held-out", str(bad), "--json"])
            self.assertNotEqual(rc, 0)
            cli = json.loads(buf.getvalue())
            self.assertFalse(cli["ok"])

    def test_empty_pairs_fails(self):
        from diptych.predictive import (
            HELD_OUT_SCHEMA_ID,
            HELD_OUT_SCHEMA_VERSION,
            build_predictive_report,
        )

        with tempfile.TemporaryDirectory() as tmp:
            empty = Path(tmp) / "empty.json"
            empty.write_text(
                json.dumps(
                    {
                        "schema": HELD_OUT_SCHEMA_ID,
                        "schema_version": HELD_OUT_SCHEMA_VERSION,
                        "pairs": [],
                    }
                ),
                encoding="utf-8",
            )
            report = build_predictive_report(held_out=empty)
            self.assertFalse(report["ok"])
            self.assertTrue(
                any("held_out_pairs_empty" in f for f in report["failures"]),
                report["failures"],
            )
            self.assertEqual(report["results"]["probe_held_out_rank_corr"], "N/A")

    def test_wrong_schema_id_fails(self):
        from diptych.predictive import HELD_OUT_SCHEMA_VERSION, build_predictive_report

        with tempfile.TemporaryDirectory() as tmp:
            wrong = Path(tmp) / "wrong.json"
            wrong.write_text(
                json.dumps(
                    {
                        "schema": "not.the.schema",
                        "schema_version": HELD_OUT_SCHEMA_VERSION,
                        "pairs": [
                            {
                                "probe_score": 1.0,
                                "trace_score": 0.0,
                                "held_out_outcome": 1.0,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            report = build_predictive_report(held_out=wrong)
            self.assertFalse(report["ok"])
            self.assertTrue(
                any("held_out_schema_id" in f for f in report["failures"]),
                report["failures"],
            )


class TestPredictiveUsablePairsStayHonest(unittest.TestCase):
    def test_single_pair_stays_na(self):
        """One pair is insufficient for rank corr — stay N/A, do not invent."""
        from diptych.predictive import (
            HELD_OUT_SCHEMA_ID,
            HELD_OUT_SCHEMA_VERSION,
            NA,
            build_predictive_report,
        )

        with tempfile.TemporaryDirectory() as tmp:
            one = Path(tmp) / "one.json"
            one.write_text(
                json.dumps(
                    {
                        "schema": HELD_OUT_SCHEMA_ID,
                        "schema_version": HELD_OUT_SCHEMA_VERSION,
                        "pairs": [
                            {
                                "id": "only",
                                "probe_score": 0.5,
                                "trace_score": 0.1,
                                "held_out_outcome": 0.9,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            report = build_predictive_report(held_out=one)
            self.assertTrue(report["ok"], report.get("failures"))
            self.assertEqual(report["status"], "held_out_loaded")
            self.assertEqual(report["n_pairs"], 1)
            self.assertEqual(report["results"]["probe_held_out_rank_corr"], NA)
            self.assertEqual(report["results"]["trace_only_rank_corr"], NA)
            self.assertEqual(report["results"]["probe_beats_trace_baseline"], NA)


class TestSmokePredictiveStep(unittest.TestCase):
    def test_smoke_includes_predictive_step(self):
        from diptych.smoke import SMOKE_SCHEMA, SMOKE_STEP_IDS, run_smoke

        self.assertEqual(SMOKE_SCHEMA, "1.8")
        self.assertIn("predictive", SMOKE_STEP_IDS)
        report = run_smoke()
        self.assertTrue(report["ok"], report.get("failures"))
        self.assertEqual(report["smoke_schema"], "1.8")
        ids = [s["id"] for s in report["steps"]]
        self.assertEqual(ids, list(SMOKE_STEP_IDS))
        by_id = {s["id"]: s for s in report["steps"]}
        pred = by_id["predictive"]
        self.assertTrue(pred["ok"], pred)
        detail = pred["detail"]
        self.assertEqual(detail["status"], "protocol_only")
        self.assertEqual(detail["n_pairs"], 0)
        self.assertEqual(detail["results"]["probe_held_out_rank_corr"], "N/A")
        self.assertEqual(detail["results"]["trace_only_rank_corr"], "N/A")
        self.assertIn("NOT AUROC", detail["non_claims"])
        blob = json.dumps(report).lower()
        self.assertNotIn('"auroc"', blob)
        self.assertNotIn('"lab_auroc"', blob)
        self.assertNotIn('"model_rank"', blob)


if __name__ == "__main__":
    unittest.main()
