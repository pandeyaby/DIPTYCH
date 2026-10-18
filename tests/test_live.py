"""Live envelopes share the control-envelope pipeline (diptych.live)."""

from __future__ import annotations

import copy
import json
import os
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PILOT = ROOT / "diptych" / "pilot"


def _saved_envelopes(artifact: Path, evaluate) -> tuple[dict, list[Path], str]:
    out = tempfile.mkdtemp(prefix="diptych-live-")
    os.environ["PILOT_ENVELOPE_DIR"] = out
    try:
        result = evaluate(str(artifact))
    finally:
        del os.environ["PILOT_ENVELOPE_DIR"]
    return result, sorted(Path(out).rglob("*.json")), out


class TestLiveContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from diptych.pilot.probes import evaluate

        cls.result, cls.paths, cls.dir = _saved_envelopes(PILOT / "controls" / "violating" / "signflip.py", evaluate)
        cls.docs = [json.loads(p.read_text()) for p in cls.paths]

    def _one(self, requirement: str) -> dict:
        return copy.deepcopy(next(d for d in self.docs if d["requirement"] == requirement))

    def test_envelopes_are_schema_0_2_live(self):
        from diptych import SCHEMA
        from diptych.live import validate_live_envelope

        self.assertGreater(len(self.docs), 0)
        for d in self.docs:
            self.assertEqual(d["diptych_schema"], SCHEMA)
            self.assertEqual(d["source"], "live")
            validate_live_envelope(d)

    def test_offline_regrade_reproduces_study_verdicts(self):
        """diptych-grade over the saved envelopes gives the verdicts evaluate() reported."""
        from diptych.grade import build_grade_report
        from diptych.pilot.predicates import REQUIREMENT

        report = build_grade_report(self.dir)
        self.assertEqual(report["rejected"], 0)
        by_req: dict[str, list[str]] = {}
        for e in report["results"]:
            by_req.setdefault(e["requirement"], []).append(e["actual_verdict"])
        for op, req in REQUIREMENT.items():
            verdicts = by_req[req]
            combined = "fail" if "fail" in verdicts else "pass" if "pass" in verdicts else "inconclusive"
            self.assertEqual(combined, self.result["paired"][op], op)

    def test_violator_fails_through_shared_grader(self):
        from diptych.grade import grade_document

        verdicts = {grade_document(d).actual_verdict for d in self.docs
                    if d["requirement"] == "asymmetric-response"}
        self.assertIn("fail", verdicts)

    def test_crn_stream_mismatch_is_inconclusive(self):
        from diptych.grade import grade_document

        d = self._one("volatility-suppression")
        d["traces"][1]["meta"]["crn_stream_id"] = "other-stream"
        self.assertEqual(grade_document(d).actual_verdict, "inconclusive")

    def test_unequal_branch_lengths_are_inconclusive(self):
        from diptych.grade import grade_document

        d = self._one("trend-recency")
        d["traces"][1]["channels"]["limit"]["values"].pop()
        self.assertEqual(grade_document(d).actual_verdict, "inconclusive")

    def test_missing_twin_is_inconclusive(self):
        from diptych.grade import grade_document

        d = self._one("trend-recency")
        d["traces"] = d["traces"][:1]
        self.assertEqual(grade_document(d).actual_verdict, "inconclusive")

    def test_contract_rejections(self):
        from diptych import ContractError
        from diptych.grade import grade_document

        d = self._one("trend-recency")
        d["expected_verdict"] = "pass"  # live envelopes carry no expected verdict
        with self.assertRaises(ContractError):
            grade_document(d)
        d = self._one("trend-recency")
        d["requirement"] = "no-such-requirement"
        with self.assertRaises(ContractError):
            grade_document(d)
        d = self._one("trend-recency")
        d["meta"]["auroc"] = 0.9
        with self.assertRaises(ContractError):
            grade_document(d)


class TestCongestionTask(unittest.TestCase):
    CONTROLS = PILOT / "congestion" / "controls"

    @classmethod
    def setUpClass(cls):
        from diptych.pilot.congestion.probes import evaluate

        cls.evaluate = staticmethod(evaluate)
        cls.conforming, cls.paths, cls.dir = _saved_envelopes(cls.CONTROLS / "conforming.py", evaluate)

    def test_conforming_passes_every_grader(self):
        self.assertEqual(self.conforming["errors"], [])
        for grader in ("paired", "single_trace", "single_trace_strong"):
            self.assertEqual(set(self.conforming[grader].values()), {"pass"}, grader)
        self.assertTrue(self.conforming["fork_fidelity"]["ok"])

    def test_each_violator_fails_its_own_requirement(self):
        from diptych.pilot.congestion.probes import KEYS

        for key in KEYS:
            with self.subTest(requirement=key):
                r = self.evaluate(str(self.CONTROLS / "violating" / f"{key.replace('-', '_')}.py"))
                self.assertEqual(r["paired"][key], "fail", r["paired"])

    def test_paired_requirements_go_through_live_envelopes(self):
        from diptych.grade import build_grade_report
        from diptych.pilot.congestion.predicates import PAIRED

        report = build_grade_report(self.dir)
        self.assertEqual(report["rejected"], 0)
        self.assertEqual({e["requirement"] for e in report["results"]}, set(PAIRED))
        self.assertEqual({e["actual_verdict"] for e in report["results"]}, {"pass"})

    def test_live_only_operators_are_rejected_in_control_envelopes(self):
        from diptych import OPERATORS
        from diptych.live import LIVE_OPERATORS

        self.assertNotIn("REFSWAP", OPERATORS)
        self.assertIn("REFSWAP", LIVE_OPERATORS)


if __name__ == "__main__":
    unittest.main()
