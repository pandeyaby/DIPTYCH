"""Coupling-discipline gate: open_loop vs crn_closed_loop."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MISMATCH = ROOT / "tests" / "fixtures" / "coupling_mismatch"


def _env() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)
    return env


class TestCouplingCanonicalMap(unittest.TestCase):
    def test_canonical_matches_operators_and_crn(self):
        from diptych import CRN_REQUIRED, OPERATORS
        from diptych.coupling import CANONICAL_COUPLING, expected_coupling

        self.assertEqual(set(CANONICAL_COUPLING), set(OPERATORS))
        open_loop = {
            "FREEZEDRY",
            "RESEED",
            "SCHEMAX",
            "SIGNFLIP",
            "SATEXTEND",
            "HISTSWAP",
        }
        crn = {"TRAJSWAP", "VARSCALE"}
        self.assertEqual(
            {op for op, c in CANONICAL_COUPLING.items() if c == "open_loop"},
            open_loop,
        )
        self.assertEqual(
            {op for op, c in CANONICAL_COUPLING.items() if c == "crn_closed_loop"},
            crn,
        )
        self.assertEqual(set(CRN_REQUIRED), crn)
        for op in OPERATORS:
            self.assertEqual(expected_coupling(op), CANONICAL_COUPLING[op])


class TestCouplingRealTreeOk(unittest.TestCase):
    def test_build_report_ok_on_repo(self):
        from diptych import OPERATORS
        from diptych.coupling import build_coupling_report

        report = build_coupling_report()
        self.assertTrue(report["ok"], report.get("failures"))
        self.assertEqual(report["failures"], [])
        self.assertEqual(report["mismatch_count"], 0)
        # 8 ops specs + every cassette probe.json
        cassette_probes = list(
            (ROOT / "examples" / "fixtures" / "cassette").rglob("probe.json")
        )
        self.assertEqual(report["check_count"], len(OPERATORS) + len(cassette_probes))
        for entry in report["checks"]:
            self.assertTrue(entry["ok"], entry)
            self.assertEqual(entry["status"], "ok")
            self.assertEqual(entry["expected"], entry["actual"])

    def test_cli_check_json_exit_zero(self):
        proc = subprocess.run(
            [sys.executable, "-m", "diptych.coupling", "--check", "--json"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=False,
            env=_env(),
        )
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        report = json.loads(proc.stdout)
        self.assertTrue(report["ok"])
        self.assertEqual(report["mismatch_count"], 0)
        blob = json.dumps(report).lower()
        self.assertNotIn('"auroc"', blob)
        self.assertNotIn('"lab_auroc"', blob)

    def test_cli_check_human_ok(self):
        proc = subprocess.run(
            [sys.executable, "-m", "diptych.coupling", "--check"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=False,
            env=_env(),
        )
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        self.assertIn("COUPLING CHECK OK", proc.stdout)


class TestCouplingMismatchFixtures(unittest.TestCase):
    def test_mismatch_fixtures_fail(self):
        from diptych.coupling import build_coupling_report

        report = build_coupling_report(
            ops_root=MISMATCH / "ops",
            cassette=MISMATCH / "cassette",
        )
        self.assertFalse(report["ok"])
        self.assertGreater(report["mismatch_count"], 0)
        fails = set(report["failures"])
        # Deliberate drifts: FREEZEDRY/TRAJSWAP in ops + cassette.
        self.assertTrue(
            any("freezedry" in p.lower() for p in fails),
            fails,
        )
        self.assertTrue(
            any("trajswap" in p.lower() for p in fails),
            fails,
        )
        by_path = {c["path"]: c for c in report["checks"] if not c["ok"]}
        # Spec drifts
        fd_spec = next(c for c in report["checks"] if c["path"].endswith("freezedry/spec.yaml"))
        self.assertFalse(fd_spec["ok"])
        self.assertEqual(fd_spec["expected"], "open_loop")
        self.assertEqual(fd_spec["actual"], "crn_closed_loop")
        ts_spec = next(c for c in report["checks"] if c["path"].endswith("trajswap/spec.yaml"))
        self.assertFalse(ts_spec["ok"])
        self.assertEqual(ts_spec["expected"], "crn_closed_loop")
        self.assertEqual(ts_spec["actual"], "open_loop")
        # Cassette probe drifts
        fd_probe = next(
            c
            for c in report["checks"]
            if "FREEZEDRY" in c["path"] and c["path"].endswith("probe.json")
        )
        self.assertFalse(fd_probe["ok"])
        self.assertEqual(fd_probe["expected"], "open_loop")
        self.assertEqual(fd_probe["actual"], "crn_closed_loop")
        _ = by_path  # used for debugging when assertions fail

    def test_cli_mismatch_exits_nonzero(self):
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "diptych.coupling",
                "--check",
                "--json",
                "--ops",
                str(MISMATCH / "ops"),
                "--cassette",
                str(MISMATCH / "cassette"),
            ],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=False,
            env=_env(),
        )
        self.assertNotEqual(proc.returncode, 0, proc.stdout)
        report = json.loads(proc.stdout)
        self.assertFalse(report["ok"])
        self.assertGreater(report["mismatch_count"], 0)


class TestSmokeCouplingStep(unittest.TestCase):
    def test_smoke_includes_coupling_check_step(self):
        from diptych.smoke import SMOKE_SCHEMA, SMOKE_STEP_IDS, run_smoke

        self.assertEqual(SMOKE_SCHEMA, "1.8")
        self.assertIn("coupling_check", SMOKE_STEP_IDS)
        report = run_smoke()
        self.assertTrue(report["ok"], report.get("failures"))
        self.assertEqual(report["smoke_schema"], "1.8")
        ids = [s["id"] for s in report["steps"]]
        self.assertEqual(ids, list(SMOKE_STEP_IDS))
        by_id = {s["id"]: s for s in report["steps"]}
        step = by_id["coupling_check"]
        self.assertTrue(step["ok"], step)
        detail = step["detail"]
        self.assertEqual(detail["mismatch_count"], 0)
        self.assertGreater(detail["check_count"], 0)
        blob = json.dumps(report).lower()
        self.assertNotIn('"auroc"', blob)


if __name__ == "__main__":
    unittest.main()
