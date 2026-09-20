"""Semantic witnesses + wrong-axis rejection for gate_axis_mutate / probe-tree."""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class TestSemanticAxisWitness(unittest.TestCase):
    def test_axis_specs_cover_all_eight(self):
        from diptych import OPERATORS
        from diptych.mutate_axis import AXIS_SPECS, MUTATORS, WRONG_AXIS_MUTATORS

        self.assertEqual(set(AXIS_SPECS), set(OPERATORS))
        self.assertEqual(set(MUTATORS), set(OPERATORS))
        self.assertEqual(set(WRONG_AXIS_MUTATORS), set(OPERATORS))
        for op, spec in AXIS_SPECS.items():
            self.assertTrue(spec["expected_axis"], op)
            self.assertTrue(spec["channel"], op)
            self.assertIn(spec["coupling"], ("open_loop", "crn_closed_loop"), op)

    def test_correct_axis_mutate_flips_with_semantic_witness(self):
        """Correct-axis mutate: pass→fail + witness fields operator-specific."""
        from diptych import OPERATORS
        from diptych.contract import load_probe
        from diptych.gates import gate_axis_mutate
        from diptych.mutate_axis import AXIS_SPECS

        for op in OPERATORS:
            conf = load_probe(ROOT / "diptych-probes" / op / "conforming" / "probe.json")
            failures, evidence = gate_axis_mutate(op, conf)
            self.assertEqual(failures, [], f"{op}: {[f.__dict__ for f in failures]}")
            self.assertTrue(evidence["power_ok"], op)
            self.assertEqual(evidence["baseline_verdict"], "pass", op)
            self.assertEqual(evidence["mutated_verdict"], "fail", op)

            wit = evidence["semantic_witness"]
            self.assertIsInstance(wit, dict, op)
            for key in ("operator", "expected_axis", "channel", "before", "after", "axis_match"):
                self.assertIn(key, wit, f"{op}.{key}")
            self.assertEqual(wit["operator"], op)
            self.assertEqual(wit["expected_axis"], AXIS_SPECS[op]["expected_axis"], op)
            self.assertEqual(wit["channel"], AXIS_SPECS[op]["channel"], op)
            self.assertTrue(wit["axis_match"], op)
            self.assertNotEqual(wit["before"], wit["after"], op)
            self.assertEqual(evidence["expected_axis"], AXIS_SPECS[op]["expected_axis"], op)
            self.assertEqual(evidence["channel"], AXIS_SPECS[op]["channel"], op)

    def test_wrong_axis_mutate_does_not_falsely_flip(self):
        """Wrong-axis distractor: conforming still passes; gate rejects power."""
        from diptych import OPERATORS
        from diptych.contract import load_probe
        from diptych.gates import gate_axis_mutate
        from diptych.grade import grade_document
        from diptych.mutate_axis import AXIS_SPECS, mutate_wrong_axis
        from diptych.probe_tree import execute_wrong_axis_branch

        for op in OPERATORS:
            conf = load_probe(ROOT / "diptych-probes" / op / "conforming" / "probe.json")
            mutated = mutate_wrong_axis(conf, op)
            after = grade_document(mutated)
            self.assertEqual(
                after.actual_verdict,
                "pass",
                f"{op}: wrong-axis falsely flipped ({after.reason})",
            )

            failures, evidence = gate_axis_mutate(op, conf, mutator=lambda d, o=op: mutate_wrong_axis(d, o))
            self.assertFalse(evidence["power_ok"], op)
            self.assertTrue(any(f.gate == "axis_mutate" for f in failures), op)
            wit = evidence.get("semantic_witness")
            # Rejected either as wrong claimed_axis or as no flip / no axis change.
            self.assertTrue(
                wit is None
                or wit.get("axis_match") is False
                or wit.get("before") == wit.get("after")
                or evidence.get("mutated_verdict") != "fail",
                f"{op}: {[f.__dict__ for f in failures]} wit={wit}",
            )

            branch = execute_wrong_axis_branch(op, conf)
            self.assertFalse(branch["falsely_flipped"], op)
            self.assertEqual(branch["mutated_verdict"], "pass", op)
            self.assertEqual(branch["semantic_witness"]["expected_axis"], AXIS_SPECS[op]["expected_axis"])
            self.assertFalse(branch["semantic_witness"]["axis_match"], op)

    def test_witness_before_after_are_operator_specific(self):
        """Each operator's witness payload keys differ by graded axis."""
        from diptych import OPERATORS
        from diptych.contract import load_probe
        from diptych.mutate_axis import AXIS_SPECS, build_semantic_witness, mutate_axis

        expected_keys = {
            "RESEED": {"stability", "epsilon", "seeds"},
            "SCHEMAX": {"keys"},
            "FREEZEDRY": {"freeze_channels", "frozen", "decision_fingerprint", "graded"},
            "SIGNFLIP": {"signflip_channel", "values", "sign_normalized_fingerprint"},
            "SATEXTEND": {"sat_channel", "values", "sat", "legal"},
            "HISTSWAP": {"history", "alt_history", "hist_splice_at", "history_corrupt"},
            "TRAJSWAP": {
                "trajectory",
                "swapped_trajectory",
                "closed_loop_residual",
                "residual_bound",
            },
            "VARSCALE": {"var_scale", "var_scale_bound", "variance_proxy"},
        }

        for op in OPERATORS:
            conf = load_probe(ROOT / "diptych-probes" / op / "conforming" / "probe.json")
            mutated = mutate_axis(conf, op)
            wit = build_semantic_witness(op, conf, mutated)
            self.assertEqual(wit.expected_axis, AXIS_SPECS[op]["expected_axis"], op)
            self.assertIsInstance(wit.before, dict, op)
            self.assertEqual(set(wit.before), expected_keys[op], op)
            self.assertEqual(set(wit.after), expected_keys[op], op)
            self.assertNotEqual(wit.before, wit.after, op)

    def test_probe_tree_full8_ok(self):
        from diptych import OPERATORS
        from diptych.contract import load_probe
        from diptych.probe_tree import execute_full8_probe_trees, execute_operator_probe_tree

        def load_conf(op: str):
            return load_probe(ROOT / "diptych-probes" / op / "conforming" / "probe.json")

        report = execute_full8_probe_trees(load_conf)
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["operator_count"], 8)
        for op in OPERATORS:
            tree = report["trees"][op]
            self.assertTrue(tree["ok"], tree)
            correct = tree["branches"]["mutate_axis"]
            wrong = tree["branches"]["wrong_axis"]
            self.assertTrue(correct["power_ok"], op)
            self.assertFalse(wrong["falsely_flipped"], op)
            wit = correct["semantic_witness"]
            self.assertEqual(wit["before"] != wit["after"], True, op)
            solo = execute_operator_probe_tree(op, load_conf(op))
            self.assertTrue(solo["ok"], op)

    def test_poc_json_includes_semantic_witness(self):
        from diptych import OPERATORS
        from diptych.mutate_axis import AXIS_SPECS
        from diptych.poc import build_poc_report

        poc = build_poc_report()
        self.assertTrue(poc["ok"], poc.get("failures"))
        for op in OPERATORS:
            power = poc["operators"][op]["gate_axis_mutate"]
            self.assertEqual(power["expected_axis"], AXIS_SPECS[op]["expected_axis"], op)
            self.assertEqual(power["channel"], AXIS_SPECS[op]["channel"], op)
            wit = power["semantic_witness"]
            self.assertIsNotNone(wit, op)
            self.assertEqual(wit["expected_axis"], AXIS_SPECS[op]["expected_axis"], op)
            self.assertIn("before", wit)
            self.assertIn("after", wit)
            self.assertNotEqual(wit["before"], wit["after"], op)
            branch = power.get("probe_tree_branch")
            self.assertIsNotNone(branch, op)
            self.assertTrue(branch["power_ok"], op)


if __name__ == "__main__":
    unittest.main()
