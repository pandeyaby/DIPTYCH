"""CODE-FIRST #18: full-8 cassette fixtures + horizon/CRN inconclusive + amortization."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "examples" / "fixtures"
CASSETTE = FIXTURES / "cassette"
OPS = ROOT / "ops"

from diptych import OPERATORS  # single source of truth for the eight operators


def _cli_env() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)
    return env


class TestFull8CassetteFixtures(unittest.TestCase):
    def test_all_eight_ops_have_conforming_and_violating(self):
        for op in OPERATORS:
            for role, verdict in (("conforming", "pass"), ("violating", "fail")):
                path = CASSETTE / op / role / "probe.json"
                self.assertTrue(path.is_file(), f"missing {path}")
                doc = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(doc["operator"], op)
                self.assertEqual(doc["source"], "diptych_core")
                self.assertEqual(doc["control_role"], role)
                self.assertEqual(doc["expected_verdict"], verdict)
                self.assertEqual(doc["diptych_schema"], "0.2")

    def test_cassette_directory_grades_green(self):
        from diptych.grade import build_grade_report

        report = build_grade_report(CASSETTE)
        self.assertTrue(report["ok"], report)
        self.assertGreaterEqual(report["graded"], 16)  # 8×2 at minimum
        self.assertEqual(report["rejected"], 0)
        self.assertEqual(report["mismatches"], 0)

    def test_each_op_conforming_pass_violating_fail(self):
        from diptych import grade_operator, parse_probe

        for op in OPERATORS:
            conf = grade_operator(parse_probe(CASSETTE / op / "conforming" / "probe.json"))
            viol = grade_operator(parse_probe(CASSETTE / op / "violating" / "probe.json"))
            self.assertEqual(conf.actual_verdict, "pass", op)
            self.assertEqual(viol.actual_verdict, "fail", op)
            self.assertTrue(conf.matches_expected, op)
            self.assertTrue(viol.matches_expected, op)


class TestInconclusiveHorizonAndCrn(unittest.TestCase):
    def _assert_inconclusive(self, path: Path, needle: str) -> None:
        from diptych import grade_operator, parse_probe

        env = parse_probe(path)
        self.assertEqual(env.expected_verdict, "inconclusive")
        result = grade_operator(env)
        self.assertEqual(result.actual_verdict, "inconclusive")
        self.assertTrue(result.matches_expected)
        self.assertIn(needle, result.reason)
        meta = result.to_dict().get("meta") or {}
        self.assertEqual(meta.get("inconclusive_reason"), result.reason)

    def test_reseed_shared_prefix_length_mismatch(self):
        self._assert_inconclusive(
            CASSETTE / "RESEED" / "inconclusive_horizon" / "probe.json",
            "shared prefix",
        )

    def test_signflip_shared_prefix_length_mismatch(self):
        self._assert_inconclusive(
            CASSETTE / "SIGNFLIP" / "inconclusive_prefix" / "probe.json",
            "shared prefix",
        )

    def test_trajswap_crn_stream_mismatch(self):
        self._assert_inconclusive(
            CASSETTE / "TRAJSWAP" / "inconclusive_crn" / "probe.json",
            "crn_stream_id",
        )
        # Hook is documented on the CRN operator spec.
        spec = (OPS / "trajswap" / "spec.yaml").read_text(encoding="utf-8")
        self.assertIn("crn_stream_id mismatch", spec)

    def test_varscale_crn_stream_mismatch(self):
        self._assert_inconclusive(
            CASSETTE / "VARSCALE" / "inconclusive_crn" / "probe.json",
            "crn_stream_id",
        )
        spec = (OPS / "varscale" / "spec.yaml").read_text(encoding="utf-8")
        self.assertIn("crn_stream_id mismatch", spec)

    def test_schemax_missing_twin_role(self):
        self._assert_inconclusive(
            CASSETTE / "SCHEMAX" / "inconclusive_missing_twin" / "probe.json",
            "missing twin role",
        )

    def test_grade_report_surfaces_meta_inconclusive_reason(self):
        from diptych.grade import build_grade_report

        report = build_grade_report(
            CASSETTE / "TRAJSWAP" / "inconclusive_crn" / "probe.json"
        )
        self.assertTrue(report["ok"])
        entry = report["results"][0]
        self.assertEqual(entry["actual_verdict"], "inconclusive")
        self.assertIn("meta", entry)
        self.assertIn("crn_stream_id", entry["meta"]["inconclusive_reason"])


class TestAmortizationCounters(unittest.TestCase):
    def test_prefix_share_protocol_fields(self):
        from diptych.probe_tree import prefix_share_amortization

        counters = prefix_share_amortization(8)
        self.assertEqual(counters["shared_prefix_nodes"], 8)
        self.assertEqual(counters["branch_suffix_nodes"], 2)
        self.assertEqual(counters["nodes_naive_two_probes"], 16)
        self.assertEqual(counters["nodes_with_prefix_share"], 10)
        self.assertEqual(counters["nodes_saved_by_prefix_share"], 6)
        # Protocol fields only — no dollar / timing keys.
        banned = ("usd", "dollar", "latency_ms", "wall_time", "auroc", "accuracy")
        blob = json.dumps(counters).lower()
        for b in banned:
            self.assertNotIn(b, blob)

    def test_gate_axis_mutate_emits_amortization(self):
        from diptych.api import parse_probe, run_gate_axis_mutate

        env = parse_probe(CASSETTE / "RESEED" / "conforming" / "probe.json")
        ok, evidence = run_gate_axis_mutate(env.operator, env)
        self.assertTrue(ok)
        amort = evidence.get("amortization") or {}
        self.assertEqual(amort.get("shared_prefix_nodes"), 8)
        self.assertIn("nodes_with_prefix_share", amort)

    def test_poc_json_includes_amortization(self):
        from diptych.poc import build_poc_report

        poc = build_poc_report()
        self.assertTrue(poc["ok"])
        for op in OPERATORS:
            amort = (poc["operators"][op]["gate_axis_mutate"] or {}).get("amortization")
            self.assertIsInstance(amort, dict, op)
            self.assertIn("shared_prefix_nodes", amort, op)


class TestGradeCliFullCassette(unittest.TestCase):
    def test_cli_grades_cassette_tree(self):
        proc = subprocess.run(
            [sys.executable, "-m", "diptych.grade", "--input", str(CASSETTE)],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=False,
            env=_cli_env(),
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        report = json.loads(proc.stdout)
        self.assertTrue(report["ok"])
        self.assertGreaterEqual(report["graded"], 16)


if __name__ == "__main__":
    unittest.main()
