"""Adapter cassette/fixture ingest + grade CLI (CONTRACT v0.2 disk path)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "examples" / "fixtures"
INVALID = ROOT / "tests" / "fixtures" / "invalid"


def _cli_env() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)
    return env


class TestCassetteFixturesGrade(unittest.TestCase):
    def test_zeroday_reseed_conforming_pass(self):
        from diptych import grade_operator, parse_probe

        path = FIXTURES / "zeroday" / "RESEED" / "conforming" / "probe.json"
        env = parse_probe(path)
        self.assertEqual(env.source, "zeroday")
        self.assertEqual(env.operator, "RESEED")
        result = grade_operator(env)
        self.assertEqual(result.actual_verdict, "pass")
        self.assertTrue(result.matches_expected)

    def test_zeroday_reseed_violating_fail(self):
        from diptych import grade_operator, parse_probe

        path = FIXTURES / "zeroday" / "RESEED" / "violating" / "probe.json"
        result = grade_operator(parse_probe(path))
        self.assertEqual(result.actual_verdict, "fail")
        self.assertTrue(result.matches_expected)

    def test_aomb_signflip_conforming_pass(self):
        from diptych import grade_operator, parse_probe

        path = FIXTURES / "aomb" / "SIGNFLIP" / "conforming" / "probe.json"
        env = parse_probe(path)
        self.assertEqual(env.source, "aomb")
        result = grade_operator(env)
        self.assertEqual(result.actual_verdict, "pass")
        self.assertTrue(result.matches_expected)

    def test_aomb_signflip_violating_fail(self):
        from diptych import grade_operator, parse_probe

        path = FIXTURES / "aomb" / "SIGNFLIP" / "violating" / "probe.json"
        result = grade_operator(parse_probe(path))
        self.assertEqual(result.actual_verdict, "fail")
        self.assertTrue(result.matches_expected)


class TestThinInvalidRejected(unittest.TestCase):
    def test_thin_empty_channels_rejected(self):
        from diptych import ContractError, parse_probe

        with self.assertRaises(ContractError) as ctx:
            parse_probe(INVALID / "thin_reseed.json")
        self.assertIn("thin envelope", str(ctx.exception))

    def test_missing_hard_key_rejected(self):
        from diptych import ContractError, parse_probe

        with self.assertRaises(ContractError) as ctx:
            parse_probe(INVALID / "missing_hard_key.json")
        self.assertIn("missing hard key", str(ctx.exception))

    def test_cli_rejects_thin_fixture(self):
        from diptych.grade import build_grade_report

        report = build_grade_report(INVALID / "thin_reseed.json")
        self.assertFalse(report["ok"])
        self.assertEqual(report["rejected"], 1)
        self.assertEqual(report["results"][0]["status"], "rejected")
        self.assertIn("thin envelope", report["results"][0]["error"])


class TestInconclusiveComparability(unittest.TestCase):
    def test_horizon_length_mismatch_inconclusive(self):
        from diptych import grade_operator, parse_probe

        path = FIXTURES / "cassette" / "RESEED" / "inconclusive_horizon" / "probe.json"
        env = parse_probe(path)
        self.assertEqual(env.expected_verdict, "inconclusive")
        result = grade_operator(env)
        self.assertEqual(result.actual_verdict, "inconclusive")
        self.assertTrue(result.matches_expected)
        self.assertIn("shared prefix", result.reason)

    def test_comparability_length_mismatch(self):
        from diptych.grade import comparability_reason, grade_document

        doc = json.loads(
            (FIXTURES / "zeroday" / "RESEED" / "conforming" / "probe.json").read_text(
                encoding="utf-8"
            )
        )
        # Truncate twin B → shared-prefix incomparable.
        doc["traces"][1]["channels"]["stability"]["values"] = [0.5, 0.5]
        doc["expected_verdict"] = "inconclusive"
        doc["meta"] = {
            "inconclusive_reason": "traces incomparable under shared prefix rule"
        }
        self.assertIsNotNone(comparability_reason(doc))
        result = grade_document(doc)
        self.assertEqual(result.actual_verdict, "inconclusive")

    def test_comparability_crn_stream_mismatch(self):
        from diptych.grade import comparability_reason, grade_document

        path = ROOT / "diptych-probes" / "TRAJSWAP" / "conforming" / "probe.json"
        doc = json.loads(path.read_text(encoding="utf-8"))
        doc["traces"][1]["meta"]["crn_stream_id"] = "other_stream"
        doc["expected_verdict"] = "inconclusive"
        doc["meta"] = {
            "inconclusive_reason": "traces incomparable under shared prefix rule"
        }
        self.assertIn("crn_stream_id", comparability_reason(doc) or "")
        result = grade_document(doc)
        self.assertEqual(result.actual_verdict, "inconclusive")

    def test_inconclusive_without_reason_rejected(self):
        from diptych import ContractError, validate_probe

        doc = json.loads(
            (FIXTURES / "zeroday" / "RESEED" / "conforming" / "probe.json").read_text(
                encoding="utf-8"
            )
        )
        doc["expected_verdict"] = "inconclusive"
        with self.assertRaises(ContractError) as ctx:
            validate_probe(doc)
        self.assertIn("inconclusive_reason", str(ctx.exception))


class TestDirectoryMode(unittest.TestCase):
    def test_multi_file_directory_grades(self):
        from diptych.grade import build_grade_report

        report = build_grade_report(FIXTURES / "zeroday" / "RESEED")
        self.assertEqual(report["count"], 2)
        self.assertEqual(report["graded"], 2)
        self.assertEqual(report["rejected"], 0)
        self.assertTrue(report["ok"])
        verdicts = {r["control_role"]: r["actual_verdict"] for r in report["results"]}
        self.assertEqual(verdicts["conforming"], "pass")
        self.assertEqual(verdicts["violating"], "fail")
        for r in report["results"]:
            self.assertEqual(r["schema_version"], "0.2")
            self.assertEqual(r["diptych_schema"], "0.2")
            self.assertEqual(r["operator"], "RESEED")

    def test_directory_with_axis_mutate(self):
        from diptych.grade import build_grade_report

        report = build_grade_report(
            FIXTURES / "aomb" / "SIGNFLIP" / "conforming",
            with_axis_mutate=True,
        )
        self.assertTrue(report["ok"])
        entry = report["results"][0]
        self.assertIn("gate_axis_mutate", entry)
        self.assertTrue(entry["gate_axis_mutate"]["power_ok"])
        self.assertEqual(entry["gate_axis_mutate"]["baseline_verdict"], "pass")
        self.assertEqual(entry["gate_axis_mutate"]["mutated_verdict"], "fail")

    def test_mixed_directory_rejects_invalid(self):
        from diptych.grade import build_grade_report

        with tempfile.TemporaryDirectory() as tmp:
            mixed = Path(tmp)
            good = mixed / "good.json"
            bad = mixed / "bad.json"
            good.write_text(
                (FIXTURES / "zeroday" / "RESEED" / "conforming" / "probe.json").read_text(
                    encoding="utf-8"
                ),
                encoding="utf-8",
            )
            bad.write_text(
                (INVALID / "thin_reseed.json").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            report = build_grade_report(mixed)
            self.assertFalse(report["ok"])
            self.assertEqual(report["count"], 2)
            self.assertEqual(report["graded"], 1)
            self.assertEqual(report["rejected"], 1)


class TestGradeCliModule(unittest.TestCase):
    def test_python_m_diptych_grade_single_file(self):
        path = FIXTURES / "aomb" / "SIGNFLIP" / "conforming" / "probe.json"
        proc = subprocess.run(
            [sys.executable, "-m", "diptych.grade", "--input", str(path)],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=False,
            env=_cli_env(),
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        report = json.loads(proc.stdout)
        self.assertTrue(report["ok"])
        self.assertEqual(report["grade_schema"], "1.0")
        self.assertEqual(report["results"][0]["actual_verdict"], "pass")
        self.assertEqual(report["results"][0]["operator"], "SIGNFLIP")

    def test_cli_exit_nonzero_on_reject(self):
        path = INVALID / "thin_reseed.json"
        proc = subprocess.run(
            [sys.executable, "-m", "diptych.grade", "--input", str(path)],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=False,
            env=_cli_env(),
        )
        self.assertNotEqual(proc.returncode, 0)
        report = json.loads(proc.stdout)
        self.assertFalse(report["ok"])



class TestFull8InconclusiveCoverage(unittest.TestCase):
    """Every operator has ≥1 cassette inconclusive_* that grades inconclusive."""

    def test_each_operator_has_inconclusive_fixture(self):
        from diptych import OPERATORS, grade_operator, parse_probe

        root = FIXTURES / "cassette"
        for op in OPERATORS:
            with self.subTest(operator=op):
                paths = sorted((root / op).glob("inconclusive_*/probe.json"))
                self.assertGreaterEqual(
                    len(paths),
                    1,
                    f"{op}: missing inconclusive_* cassette fixture",
                )
                for path in paths:
                    env = parse_probe(path)
                    self.assertEqual(env.operator, op)
                    self.assertEqual(env.expected_verdict, "inconclusive")
                    result = grade_operator(env)
                    self.assertEqual(
                        result.actual_verdict,
                        "inconclusive",
                        msg=f"{path}: {result.reason}",
                    )
                    self.assertTrue(result.matches_expected, result.reason)
                    self.assertTrue(
                        (result.reason or "").strip(),
                        f"{path}: empty inconclusive reason",
                    )


if __name__ == "__main__":
    unittest.main()
