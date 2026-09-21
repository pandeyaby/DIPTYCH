"""Unified RQ1–RQ5 protocol report CLI."""

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


class TestProtocolReport(unittest.TestCase):
    def test_build_protocol_report_ok(self):
        from diptych import SCHEMA
        from diptych.protocol import (
            NA,
            NON_CLAIMS,
            PROTOCOL_SCHEMA,
            RQ_IDS,
            build_protocol_report,
        )

        report = build_protocol_report()
        self.assertEqual(report["protocol_schema"], PROTOCOL_SCHEMA)
        self.assertEqual(report["diptych_schema"], SCHEMA)
        self.assertTrue(report["ok"], report.get("failures"))
        self.assertEqual(report["failures"], [])
        self.assertEqual(report["rq_count"], len(RQ_IDS))
        self.assertEqual(list(report["rqs"].keys()), list(RQ_IDS))

        rq1 = report["rqs"]["RQ1"]
        self.assertTrue(rq1["ok"])
        self.assertEqual(rq1["harness"], "separation")
        self.assertEqual(rq1["metrics"]["control_separation_index"], 1.0)
        self.assertEqual(rq1["metrics"]["separates_count"], 8)

        rq2 = report["rqs"]["RQ2"]
        self.assertTrue(rq2["ok"])
        self.assertEqual(rq2["harness"], "ablation")
        self.assertTrue(rq2["metrics"]["full8_ok"])
        self.assertEqual(rq2["metrics"]["full8_hyper_separates_count"], 8)

        rq3 = report["rqs"]["RQ3"]
        self.assertTrue(rq3["ok"])
        self.assertEqual(rq3["status"], "protocol_only")
        self.assertEqual(rq3["harness"], "predictive")
        self.assertEqual(rq3["metrics"]["probe_held_out_rank_corr"], NA)
        self.assertEqual(rq3["metrics"]["trace_only_rank_corr"], NA)
        self.assertEqual(rq3["metrics"]["probe_beats_trace_baseline"], NA)

        rq4 = report["rqs"]["RQ4"]
        self.assertTrue(rq4["ok"])
        self.assertEqual(rq4["harness"], "amortization")
        self.assertAlmostEqual(rq4["metrics"]["alpha"], 0.625)
        self.assertTrue(rq4["metrics"]["counters_consistent"])

        rq5 = report["rqs"]["RQ5"]
        self.assertTrue(rq5["ok"])
        self.assertEqual(rq5["harness"], "inconclusive")
        self.assertEqual(rq5["metrics"]["ops_with_working_inconclusive"], 8)
        self.assertEqual(rq5["metrics"]["n_inconclusive"], 8)

        for claim in NON_CLAIMS:
            self.assertIn(claim, report["non_claims"])
        blob = json.dumps(report).lower()
        self.assertNotIn('"auroc"', blob)
        self.assertNotIn('"lab_auroc"', blob)
        self.assertNotIn('"model_rank"', blob)
        self.assertNotIn('"usd"', blob)

    def test_cli_json_stdout_exit_zero(self):
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "diptych.protocol",
                "--json",
                "--stdout-only",
            ],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=False,
            env=_env(),
        )
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        report = json.loads(proc.stdout)
        self.assertTrue(report["ok"])
        self.assertEqual(report["protocol_schema"], "1.0")
        self.assertEqual(report["rqs"]["RQ3"]["status"], "protocol_only")

    def test_cli_human_ok(self):
        proc = subprocess.run(
            [sys.executable, "-m", "diptych.protocol"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=False,
            env=_env(),
        )
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        self.assertIn("PROTOCOL OK", proc.stdout)
        self.assertIn("RQ1", proc.stdout)
        self.assertIn("RQ5", proc.stdout)
        self.assertIn("protocol_only", proc.stdout)
        lower = proc.stdout.lower()
        self.assertNotIn("auroc=", lower)

    def test_cli_write_out(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "protocol.json"
            proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "diptych.protocol",
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

    def test_bad_held_out_fails_nonzero(self):
        from diptych.protocol import build_protocol_report, main

        missing = Path(tempfile.mkdtemp()) / "no_held_out.json"
        report = build_protocol_report(held_out=missing)
        self.assertFalse(report["ok"])
        self.assertIn("RQ3", report["failures"])
        self.assertFalse(report["rqs"]["RQ3"]["ok"])
        self.assertEqual(report["rqs"]["RQ3"]["status"], "held_out_invalid")
        # Other RQs should still be ok on the default cassette.
        for rq in ("RQ1", "RQ2", "RQ4", "RQ5"):
            self.assertTrue(report["rqs"][rq]["ok"], rq)

        from contextlib import redirect_stdout
        from io import StringIO

        buf = StringIO()
        with redirect_stdout(buf):
            rc = main(["--held-out", str(missing), "--json", "--stdout-only"])
        self.assertNotEqual(rc, 0)
        cli = json.loads(buf.getvalue())
        self.assertFalse(cli["ok"])
        self.assertIn("RQ3", cli["failures"])


class TestSmokeProtocolStep(unittest.TestCase):
    def test_smoke_includes_protocol_step(self):
        from diptych.smoke import SMOKE_SCHEMA, SMOKE_STEP_IDS, run_smoke

        self.assertEqual(SMOKE_SCHEMA, "1.9")
        self.assertIn("protocol", SMOKE_STEP_IDS)
        self.assertNotIn("separation", SMOKE_STEP_IDS)
        self.assertNotIn("predictive", SMOKE_STEP_IDS)
        report = run_smoke()
        self.assertTrue(report["ok"], report.get("failures"))
        self.assertEqual(report["smoke_schema"], "1.9")
        ids = [s["id"] for s in report["steps"]]
        self.assertEqual(ids, list(SMOKE_STEP_IDS))
        by_id = {s["id"]: s for s in report["steps"]}
        proto = by_id["protocol"]
        self.assertTrue(proto["ok"], proto)
        detail = proto["detail"]
        self.assertEqual(detail["rq_count"], 5)
        self.assertEqual(detail["predictive_status"], "protocol_only")
        self.assertEqual(detail["control_separation_index"], 1.0)
        self.assertAlmostEqual(detail["alpha"], 0.625)
        self.assertEqual(detail["n_inconclusive"], 8)
        self.assertEqual(detail["probe_held_out_rank_corr"], "N/A")
        for rq in ("RQ1", "RQ2", "RQ3", "RQ4", "RQ5"):
            self.assertTrue(detail["rqs"][rq]["ok"], rq)
        self.assertIn("NOT AUROC", detail["non_claims"])
        blob = json.dumps(report).lower()
        self.assertNotIn('"auroc"', blob)
        self.assertNotIn('"lab_auroc"', blob)
        self.assertNotIn('"model_rank"', blob)


if __name__ == "__main__":
    unittest.main()
