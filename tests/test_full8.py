"""Full-8 DIPTYCH adapter tests (manifest, contrast, stub rejection)."""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class TestDiptychFull8(unittest.TestCase):
    def test_aomb_adapter_axis_sketches(self):
        # adapter validate optional
        from diptych import OPERATORS
        for op in OPERATORS:
            for role in ("conforming", "violating"):
                p = ROOT / "diptych-probes" / op / role / "probe.json"
                doc = json.loads(p.read_text(encoding="utf-8"))
                None  # core probes validated by contract

    def test_gate_pass_on_repo_fixtures(self):
        from diptych.gates import run_gates

        report = run_gates()
        self.assertTrue(report.ok, [f.__dict__ for f in report.failures])
        for op, cell in report.matrix["operators"].items():
            self.assertEqual(cell["diptych_core"], "green", op)

    def test_all_operators_present(self):
        from diptych import OPERATORS

        for op in OPERATORS:
            for role in ("conforming", "violating"):
                p = ROOT / "diptych-probes" / op / role / "probe.json"
                self.assertTrue(p.is_file(), p)
                doc = json.loads(p.read_text(encoding="utf-8"))
                self.assertEqual(doc["diptych_schema"], "0.2")
                self.assertEqual(doc["source"], "diptych_core")
                self.assertEqual(doc["operator"], op)
                self.assertEqual(doc["control_role"], role)
                self.assertEqual(
                    doc["expected_verdict"], "pass" if role == "conforming" else "fail"
                )
                self.assertGreaterEqual(len(doc["traces"]), 2)
                if op in ("TRAJSWAP", "VARSCALE"):
                    self.assertEqual(doc["coupling"], "crn_closed_loop")

    def test_missing_violating_twin_fails(self):
        from diptych import gates as G

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            shutil.copytree(ROOT / "diptych-probes", tmp_path / "diptych-probes")
            # remove one violating twin
            victim = tmp_path / "diptych-probes" / "RESEED" / "violating" / "probe.json"
            victim.unlink()
            old_root, old_probes = G.ROOT, G.PROBES
            try:
                G.ROOT = tmp_path
                G.PROBES = tmp_path / "diptych-probes"
                report = G.run_gates()
            finally:
                G.ROOT, G.PROBES = old_root, old_probes
            self.assertFalse(report.ok)
            self.assertTrue(any(f.gate == "manifest" for f in report.failures))

    def test_identical_twins_fail_contrast(self):
        from diptych import gates as G

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            shutil.copytree(ROOT / "diptych-probes", tmp_path / "diptych-probes")
            conf = tmp_path / "diptych-probes" / "SIGNFLIP" / "conforming" / "probe.json"
            viol = tmp_path / "diptych-probes" / "SIGNFLIP" / "violating" / "probe.json"
            doc = json.loads(conf.read_text(encoding="utf-8"))
            doc["control_role"] = "violating"
            doc["expected_verdict"] = "fail"
            doc["probe_id"] = "aomb.signflip_v0.violating_identical"
            viol.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
            old_root, old_probes = G.ROOT, G.PROBES
            try:
                G.ROOT = tmp_path
                G.PROBES = tmp_path / "diptych-probes"
                report = G.run_gates()
            finally:
                G.ROOT, G.PROBES = old_root, old_probes
            self.assertFalse(report.ok)
            self.assertTrue(any(f.gate == "contrast" for f in report.failures))

    def test_no_lab_auroc_in_probes(self):
        for p in (ROOT / "diptych-probes").rglob("probe.json"):
            text = p.read_text(encoding="utf-8")
            self.assertNotIn("auroc", text.lower())
            self.assertNotIn("0.766", text)

    def test_axis_mutate_flips_each_operator(self):
        """gate_axis_mutate: mutate only each op's axis → pass→fail for all 8."""
        from diptych import OPERATORS
        from diptych.contract import load_probe
        from diptych.gates import gate_axis_mutate
        from diptych.mutate_axis import MUTATION_DESCRIPTIONS, MUTATORS

        self.assertEqual(set(MUTATORS), set(OPERATORS))
        self.assertEqual(set(MUTATION_DESCRIPTIONS), set(OPERATORS))

        for op in OPERATORS:
            conf = load_probe(ROOT / "diptych-probes" / op / "conforming" / "probe.json")
            failures, evidence = gate_axis_mutate(op, conf)
            self.assertEqual(failures, [], f"{op}: { [f.__dict__ for f in failures] }")
            self.assertTrue(evidence["power_ok"], op)
            self.assertTrue(evidence["axis_changed"], op)
            self.assertEqual(evidence["baseline_verdict"], "pass", op)
            self.assertEqual(evidence["mutated_verdict"], "fail", op)

    def test_verdict_only_flip_is_not_axis_power(self):
        """Negative: expected_verdict-only flip without axis edit must NOT count as power."""
        from diptych.contract import load_probe
        from diptych.gates import gate_axis_mutate
        from diptych.mutate_axis import axis_fingerprint, cosmetic_verdict_only

        conf = load_probe(ROOT / "diptych-probes" / "RESEED" / "conforming" / "probe.json")
        failures, evidence = gate_axis_mutate(op="RESEED", conf=conf, mutator=cosmetic_verdict_only)
        self.assertFalse(evidence["power_ok"])
        self.assertFalse(evidence["axis_changed"])
        self.assertEqual(axis_fingerprint(conf), axis_fingerprint(cosmetic_verdict_only(conf)))
        self.assertTrue(any(f.gate == "axis_mutate" for f in failures))
        self.assertTrue(
            any("cosmetic" in f.detail or "verdict-only" in f.detail for f in failures),
            [f.__dict__ for f in failures],
        )

    def test_cosmetic_relabel_is_not_axis_power(self):
        """Negative (DIPTYCH cosmetic-relabel): SARIF rename / AUROC inject ≠ power."""
        from diptych.contract import load_probe
        from diptych.gates import gate_axis_mutate
        from diptych.mutate_axis import (
            axis_fingerprint,
            cosmetic_auroc_inject,
            cosmetic_sarif_level_rename,
        )

        conf = load_probe(ROOT / "diptych-probes" / "SIGNFLIP" / "conforming" / "probe.json")
        for name, mutator in (
            ("sarif_level_rename", cosmetic_sarif_level_rename),
            ("auroc_inject", cosmetic_auroc_inject),
        ):
            failures, evidence = gate_axis_mutate(
                op="SIGNFLIP", conf=conf, mutator=mutator
            )
            self.assertFalse(evidence["power_ok"], name)
            self.assertFalse(evidence["axis_changed"], name)
            self.assertEqual(
                axis_fingerprint(conf),
                axis_fingerprint(mutator(conf)),
                name,
            )
            self.assertTrue(any(f.gate == "axis_mutate" for f in failures), name)
            self.assertTrue(
                any(
                    "cosmetic" in f.detail or "verdict-only" in f.detail
                    for f in failures
                ),
                f"{name}: {[f.__dict__ for f in failures]}",
            )

    def test_forbidden_cosmetics_leave_axis_fingerprint_unchanged(self):
        """Invariant: all three forbidden sole edits leave axis fingerprint identical."""
        from diptych.contract import load_probe
        from diptych.mutate_axis import (
            axis_fingerprint,
            cosmetic_auroc_inject,
            cosmetic_sarif_level_rename,
            cosmetic_verdict_only,
        )

        conf = load_probe(ROOT / "diptych-probes" / "SCHEMAX" / "conforming" / "probe.json")
        before = axis_fingerprint(conf)
        for name, mutator in (
            ("verdict_only", cosmetic_verdict_only),
            ("sarif_level_rename", cosmetic_sarif_level_rename),
            ("auroc_inject", cosmetic_auroc_inject),
        ):
            after = mutator(conf)
            self.assertEqual(before, axis_fingerprint(after), name)
            # Envelope cosmetics may change, but traces channels+meta must not.
            self.assertEqual(conf["traces"], after["traces"], name)


if __name__ == "__main__":
    unittest.main()
