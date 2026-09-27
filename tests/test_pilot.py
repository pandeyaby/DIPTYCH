"""Pilot study: executable controls must validate every paired probe."""

from __future__ import annotations

import unittest
from pathlib import Path

from diptych import OPERATORS

ROOT = Path(__file__).resolve().parents[1]
CONTROLS = ROOT / "diptych" / "pilot" / "controls"


def _evaluate(path: Path) -> dict:
    from diptych.pilot.probes import evaluate

    return evaluate(str(path))


class TestPilotPlant(unittest.TestCase):
    def test_plant_is_pure_and_crn(self):
        from diptych.pilot.plant import SCENARIOS, Plant

        a = Plant(SCENARIOS["steady"], seed=3)
        b = Plant(SCENARIOS["steady_long"], seed=3, var_scale=3.0)
        self.assertEqual(a.observe(10, 50.0), Plant(SCENARIOS["steady"], seed=3).observe(10, 50.0))
        # Same draws: identical latency noise, arrivals scaled only by variance.
        oa, ob = a.observe(10, 500.0), b.observe(10, 500.0)
        self.assertAlmostEqual(oa["arrival_rps"] - 85.0, (ob["arrival_rps"] - 85.0) / 3.0)


class TestPilotControls(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.conforming = _evaluate(CONTROLS / "conforming.py")

    def test_conforming_passes_every_paired_probe(self):
        self.assertEqual(self.conforming["errors"], [])
        self.assertEqual(set(self.conforming["paired"].values()), {"pass"})

    def test_conforming_passes_single_trace_grader(self):
        self.assertEqual(set(self.conforming["single_trace"].values()), {"pass"})

    def test_each_violator_fails_its_own_paired_probe(self):
        for op in OPERATORS:
            with self.subTest(op=op):
                r = _evaluate(CONTROLS / "violating" / f"{op.lower()}.py")
                self.assertEqual(r["paired"][op], "fail", r["paired"])

    def test_prefix_sharing_ticks_are_counted(self):
        t = self.conforming["ticks"]
        self.assertGreater(t["naive_equivalent"], t["forked"])
        self.assertGreater(t["prefix_shared"], 0)


if __name__ == "__main__":
    unittest.main()


class TestStrongBaseline(unittest.TestCase):
    def test_no_reading_flags_the_conforming_control(self):
        from diptych.pilot.probes import calibrate_strong

        self.assertEqual(calibrate_strong(), set())

    def test_strong_baseline_catches_schema_violator(self):
        # Schema drift within one trace is single-trace falsifiable.
        r = _evaluate(CONTROLS / "violating" / "schemax.py")
        self.assertEqual(r["single_trace_strong"]["SCHEMAX"], "fail")

    def test_strong_baseline_misses_trend_violator(self):
        r = _evaluate(CONTROLS / "violating" / "trajswap.py")
        self.assertEqual(r["single_trace_strong"]["TRAJSWAP"], "pass")
        self.assertEqual(r["paired"]["TRAJSWAP"], "fail")
