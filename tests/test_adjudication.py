"""Blinded adjudication: item selection, packet hygiene, scoring."""

from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path

LEAK = re.compile(r"\b(paired|verdict|diptych|violat\w*|conform\w*|predicate|claude|gpt|opus|sonnet|haiku|fable)\b", re.I)


class TestAdjudication(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from diptych.pilot.adjudication import build

        cls.out = Path(tempfile.mkdtemp(prefix="adj-"))
        build(cls.out, raters=2)
        cls.key = json.loads((cls.out / "KEY.json").read_text())

    def test_selection_is_deterministic_and_balanced(self):
        import random

        from diptych.pilot.adjudication import SEED, select_items

        a = select_items(random.Random(SEED))
        b = select_items(random.Random(SEED))
        self.assertEqual([(i["row"], i["key"]) for i in a], [(i["row"], i["key"]) for i in b])
        cats = {i["category"] for i in a}
        self.assertEqual(cats, {"paired_only", "both_pass", "both_fail", "strong_only", "control"})
        self.assertEqual(len(a), 42)

    def test_packets_carry_no_verdicts_or_hints(self):
        for pk in sorted(self.out.glob("packet-r*")):
            for f in pk.rglob("*"):
                if f.is_file() and f.name != "INSTRUCTIONS.md":
                    text = f.read_text()
                    self.assertNotIn('"paired"', text, f)
                    if f.name == "controller.py":
                        # Model-written code may say "probe"; nothing may say what we graded.
                        self.assertIsNone(LEAK.search(text), f"{f}: {LEAK.search(text)}")
            self.assertFalse((pk / "KEY.json").exists())

    def test_every_controller_loads_standalone(self):
        import importlib.util

        for f in (self.out / "packet-r1" / "items").glob("*/controller.py"):
            spec = importlib.util.spec_from_file_location("c", f)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            self.assertTrue(hasattr(mod, "Controller"), f)

    def test_score_with_synthetic_raters(self):
        from diptych.pilot.adjudication import score

        truth = {"fail": "violates", "pass": "satisfies"}
        for n, flip in ((1, False), (2, True)):
            items = {}
            for iid, it in self.key.items():
                v = truth[it["paired"]]
                if flip and it["category"] == "paired_only":
                    v = "satisfies"
                items[iid] = {"verdict": v, "confidence": 3, "method": "code reading", "rationale": "x"}
            (self.out / f"packet-r{n}" / "answers.json").write_text(json.dumps({"rater": f"r{n}", "items": items}))
        rep = score(self.out)["summary"]
        self.assertEqual(rep["items"], 42)
        controls = rep["by_category"]["A/control"]
        self.assertEqual(controls["agrees_paired"], controls["n"])

    def test_fleiss_kappa(self):
        from diptych.pilot.adjudication import fleiss_kappa

        self.assertAlmostEqual(fleiss_kappa([[5, 0, 0], [0, 5, 0], [5, 0, 0], [0, 5, 0]]), 1.0)
        self.assertLess(fleiss_kappa([[3, 2, 0], [2, 3, 0], [3, 2, 0], [2, 3, 0]]), 0.1)


if __name__ == "__main__":
    unittest.main()
