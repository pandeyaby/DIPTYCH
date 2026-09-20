"""Thin-envelope rejection corpus under tests/fixtures/thin/ (loud reject)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
THIN = ROOT / "tests" / "fixtures" / "thin"

# Fixture basename → substring that must appear in the ContractError / reject error.
EXPECT_REJECT = {
    "missing_axis.json": "thin envelope",
    "stub_marker.json": "stub marker",
    "hardcoded_pass.json": "hardcoded_pass",
    "auroc_field.json": "forbidden score",
    "wrong_schema_version.json": "diptych_schema must be",
}


def _cli_env() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)
    return env


class TestThinRejectCorpus(unittest.TestCase):
    def test_corpus_files_present(self):
        names = sorted(p.name for p in THIN.glob("*.json"))
        self.assertEqual(names, sorted(EXPECT_REJECT))

    def test_parse_probe_loud_reject_each(self):
        from diptych import ContractError, parse_probe

        for name, needle in EXPECT_REJECT.items():
            path = THIN / name
            with self.subTest(fixture=name):
                with self.assertRaises(ContractError) as ctx:
                    parse_probe(path)
                self.assertIn(needle, str(ctx.exception), name)

    def test_build_grade_report_rejects_directory(self):
        from diptych.grade import build_grade_report

        report = build_grade_report(THIN)
        self.assertFalse(report["ok"])
        self.assertEqual(report["count"], len(EXPECT_REJECT))
        self.assertEqual(report["graded"], 0)
        self.assertEqual(report["rejected"], len(EXPECT_REJECT))
        by_name = {Path(r["path"]).name: r for r in report["results"]}
        for name, needle in EXPECT_REJECT.items():
            entry = by_name[name]
            self.assertEqual(entry["status"], "rejected", name)
            self.assertIn(needle, entry["error"], name)

    def test_cli_rejects_thin_dir_nonzero(self):
        proc = subprocess.run(
            [sys.executable, "-m", "diptych.grade", "--input", str(THIN)],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=False,
            env=_cli_env(),
        )
        self.assertNotEqual(proc.returncode, 0)
        report = json.loads(proc.stdout)
        self.assertFalse(report["ok"])
        self.assertEqual(report["rejected"], len(EXPECT_REJECT))


if __name__ == "__main__":
    unittest.main()
