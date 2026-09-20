"""Per-operator diptych_schema 0.2 contract tests (typed API + thin rejection)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROBES = ROOT / "diptych-probes"


def _load_raw(op: str, role: str) -> dict:
    return json.loads((PROBES / op / role / "probe.json").read_text(encoding="utf-8"))


class TestPublicApiImports(unittest.TestCase):
    def test_from_diptych_surface(self):
        from diptych import (
            OPERATORS,
            ProbeEnvelope,
            grade_operator,
            parse_probe,
            run_gate_axis_mutate,
            validate_probe,
        )

        self.assertEqual(len(OPERATORS), 8)
        self.assertTrue(callable(parse_probe))
        self.assertTrue(callable(grade_operator))
        self.assertTrue(callable(run_gate_axis_mutate))
        self.assertTrue(callable(validate_probe))
        self.assertTrue(issubclass(ProbeEnvelope, object))

    def test_pip_editable_import_path(self):
        # Ensures package metadata layout resolves diptych.api / diptych.schema.
        import diptych.api as api
        import diptych.schema as schema

        self.assertIn("ProbeEnvelope", api.__all__)
        self.assertTrue(hasattr(schema, "ProbeEnvelopeDict"))


class TestOperatorContracts(unittest.TestCase):
    """Conforming/violating envelopes round-trip; invalid schema fails loudly."""

    def test_each_operator_round_trip_and_grade(self):
        from diptych import OPERATORS, ProbeEnvelope, grade_operator, parse_probe

        for op in OPERATORS:
            for role in ("conforming", "violating"):
                path = PROBES / op / role / "probe.json"
                env = parse_probe(path)
                self.assertIsInstance(env, ProbeEnvelope)
                self.assertEqual(env.operator, op)
                self.assertEqual(env.control_role, role)
                self.assertEqual(
                    env.expected_verdict, "pass" if role == "conforming" else "fail"
                )
                self.assertEqual(env.diptych_schema, "0.2")
                self.assertGreaterEqual(len(env.traces), 2)

                back = env.to_dict()
                again = parse_probe(back)
                self.assertEqual(again.operator, op)
                self.assertEqual(again.probe_id, env.probe_id)
                self.assertEqual(again.traces[0].trace_id, env.traces[0].trace_id)

                result = grade_operator(env)
                self.assertTrue(
                    result.matches_expected,
                    f"{op}/{role}: expected {result.expected_verdict} "
                    f"got {result.actual_verdict} ({result.reason})",
                )

    def test_each_operator_gate_axis_mutate_via_api(self):
        from diptych import OPERATORS, parse_probe, run_gate_axis_mutate

        for op in OPERATORS:
            env = parse_probe(PROBES / op / "conforming" / "probe.json")
            ok, evidence = run_gate_axis_mutate(op, env)
            self.assertTrue(ok, f"{op}: {evidence.get('failures')}")
            self.assertTrue(evidence.get("power_ok"))
            self.assertEqual(evidence.get("baseline_verdict"), "pass")
            self.assertEqual(evidence.get("mutated_verdict"), "fail")
            wit = evidence.get("semantic_witness")
            self.assertIsInstance(wit, dict, op)
            self.assertTrue(wit.get("axis_match"), op)

    def test_envelope_dataclass_fields_match_contract(self):
        from diptych import parse_probe
        from diptych.schema import CONTROL_ROLE_ENUM, COUPLING_ENUM, VERDICT_ENUM

        env = parse_probe(PROBES / "RESEED" / "conforming" / "probe.json")
        self.assertIn(env.coupling, COUPLING_ENUM)
        self.assertIn(env.control_role, CONTROL_ROLE_ENUM)
        self.assertIn(env.expected_verdict, VERDICT_ENUM)
        self.assertIn("seed", env.traces[0].meta)
        self.assertIn("stability", env.traces[0].channels)


class TestThinStubRejection(unittest.TestCase):
    def _base(self, op: str = "RESEED") -> dict:
        return _load_raw(op, "conforming")

    def test_missing_hard_key_fails(self):
        from diptych import ContractError, validate_probe

        doc = self._base()
        del doc["probe_id"]
        with self.assertRaises(ContractError) as ctx:
            validate_probe(doc)
        self.assertIn("missing hard key", str(ctx.exception))

    def test_empty_channels_rejected(self):
        from diptych import ContractError, validate_probe

        doc = self._base()
        doc["traces"][0]["channels"] = {}
        with self.assertRaises(ContractError) as ctx:
            validate_probe(doc)
        self.assertIn("thin envelope", str(ctx.exception))

    def test_empty_values_rejected(self):
        from diptych import ContractError, validate_probe

        doc = self._base()
        doc["traces"][0]["channels"]["stability"]["values"] = []
        with self.assertRaises(ContractError) as ctx:
            validate_probe(doc)
        self.assertIn("thin envelope", str(ctx.exception))

    def test_stub_marker_todo_rejected(self):
        from diptych import ContractError, validate_probe

        doc = self._base()
        doc["meta"] = {"note": "TODO"}
        with self.assertRaises(ContractError) as ctx:
            validate_probe(doc)
        self.assertIn("stub marker", str(ctx.exception))

    def test_hardcoded_pass_smell_rejected(self):
        from diptych import ContractError, validate_probe

        doc = self._base()
        doc["hardcoded_pass"] = True
        with self.assertRaises(ContractError) as ctx:
            validate_probe(doc)
        self.assertIn("hardcoded_pass", str(ctx.exception))

    def test_missing_axis_rejected_per_operator(self):
        from diptych import OPERATORS, ContractError, validate_probe

        for op in OPERATORS:
            doc = _load_raw(op, "conforming")
            # Strip operator axis payload while keeping hard keys + seeds.
            for tr in doc["traces"]:
                tr["channels"] = {"placeholder": {"values": [0.0]}}
                # Keep seed; drop axis-specific meta keys.
                seed = tr["meta"].get("seed", 0)
                crn = {
                    k: tr["meta"][k]
                    for k in ("crn_stream_id", "crn_closed_loop")
                    if k in tr["meta"]
                }
                tr["meta"] = {"seed": seed, **crn}
            with self.assertRaises(ContractError, msg=op) as ctx:
                validate_probe(doc)
            self.assertIn("thin envelope", str(ctx.exception), op)
            self.assertIn(op, str(ctx.exception), op)

    def test_forbidden_auroc_rejected(self):
        from diptych import ContractError, validate_probe

        doc = self._base()
        doc["auroc"] = 0.99
        with self.assertRaises(ContractError) as ctx:
            validate_probe(doc)
        self.assertIn("forbidden score", str(ctx.exception))

    def test_crn_ops_require_closed_loop(self):
        from diptych import ContractError, validate_probe

        doc = _load_raw("TRAJSWAP", "conforming")
        doc["coupling"] = "open_loop"
        with self.assertRaises(ContractError) as ctx:
            validate_probe(doc)
        self.assertIn("crn_closed_loop", str(ctx.exception))

    def test_role_verdict_mismatch_fails(self):
        from diptych import ContractError, validate_probe

        doc = self._base()
        doc["expected_verdict"] = "fail"  # conforming must be pass
        with self.assertRaises(ContractError) as ctx:
            validate_probe(doc)
        self.assertIn("conforming must expected_verdict=pass", str(ctx.exception))

    def test_parse_probe_from_path_and_dict(self):
        from diptych import parse_probe

        path = PROBES / "SCHEMAX" / "violating" / "probe.json"
        a = parse_probe(path)
        b = parse_probe(_load_raw("SCHEMAX", "violating"))
        self.assertEqual(a.operator, b.operator)
        self.assertEqual(a.expected_verdict, "fail")

    def test_invalid_json_schema_version_fails(self):
        from diptych import ContractError, validate_probe

        doc = self._base()
        doc["diptych_schema"] = "0.1"
        with self.assertRaises(ContractError) as ctx:
            validate_probe(doc)
        self.assertIn("diptych_schema", str(ctx.exception))

    def test_round_trip_via_tempfile(self):
        from diptych import envelope_round_trip, parse_probe

        doc = _load_raw("HISTSWAP", "conforming")
        back = envelope_round_trip(doc)
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump(back, fh)
            path = Path(fh.name)
        try:
            env = parse_probe(path)
            self.assertEqual(env.operator, "HISTSWAP")
        finally:
            path.unlink(missing_ok=True)


class TestGradeOperatorApi(unittest.TestCase):
    def test_grade_accepts_dict_envelope_and_path(self):
        from diptych import grade_operator, parse_probe

        path = PROBES / "FREEZEDRY" / "conforming" / "probe.json"
        r1 = grade_operator(path)
        r2 = grade_operator(parse_probe(path))
        r3 = grade_operator(_load_raw("FREEZEDRY", "conforming"))
        self.assertTrue(r1.matches_expected)
        self.assertEqual(r1.actual_verdict, r2.actual_verdict)
        self.assertEqual(r2.actual_verdict, r3.actual_verdict)


if __name__ == "__main__":
    unittest.main()
