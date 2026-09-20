"""Executable CONTRACT v0.2 JSON Schema tests (enums, fixtures, thin corpus)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
THIN = ROOT / "tests" / "fixtures" / "thin"
SCHEMA_DOC = ROOT / "docs" / "adapters" / "diptych_schema_0.2.json"
SCHEMA_ALT = ROOT / "diptych" / "schema" / "v0_2.json"

VALID_ROOTS = (
    ROOT / "examples" / "fixtures" / "cassette",
    ROOT / "diptych-probes",
    ROOT / "examples" / "fixtures" / "zeroday",
    ROOT / "examples" / "fixtures" / "aomb",
)

THIN_NAMES = (
    "auroc_field.json",
    "hardcoded_pass.json",
    "missing_axis.json",
    "stub_marker.json",
    "wrong_schema_version.json",
)


def _cli_env() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)
    return env


def _probe_paths() -> list[Path]:
    paths: list[Path] = []
    for root in VALID_ROOTS:
        if root.is_dir():
            paths.extend(sorted(root.rglob("probe.json")))
    return paths


class TestSchemaEnumsMatchPackage(unittest.TestCase):
    def test_operator_and_coupling_enums_match(self):
        from diptych import COUPLINGS, OPERATORS
        from diptych.schema import COUPLING_ENUM, OPERATOR_ENUM, build_json_schema

        self.assertEqual(set(OPERATOR_ENUM), set(OPERATORS))
        self.assertEqual(set(COUPLING_ENUM), set(COUPLINGS))

        schema = build_json_schema()
        self.assertEqual(schema["properties"]["operator"]["enum"], list(OPERATORS))
        self.assertEqual(
            set(schema["properties"]["coupling"]["enum"]), set(COUPLINGS)
        )
        self.assertEqual(schema["properties"]["diptych_schema"]["const"], "0.2")


class TestCommittedSchemaFresh(unittest.TestCase):
    def test_docs_and_alt_match_builder(self):
        from diptych.schema import assert_committed_schema_fresh, build_json_schema

        assert_committed_schema_fresh()
        self.assertTrue(SCHEMA_DOC.is_file(), SCHEMA_DOC)
        self.assertTrue(SCHEMA_ALT.is_file(), SCHEMA_ALT)
        generated = build_json_schema()
        self.assertEqual(json.loads(SCHEMA_DOC.read_text(encoding="utf-8")), generated)
        self.assertEqual(json.loads(SCHEMA_ALT.read_text(encoding="utf-8")), generated)

    def test_cli_check_fresh_ok(self):
        proc = subprocess.run(
            [sys.executable, "-m", "diptych.schema", "--check-fresh"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=False,
            env=_cli_env(),
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("SCHEMA FRESH OK", proc.stdout)


class TestValidFixturesPassSchema(unittest.TestCase):
    def test_all_valid_probe_json_pass_check_path(self):
        from diptych.schema import check_path

        paths = _probe_paths()
        self.assertGreaterEqual(len(paths), 16)
        for path in paths:
            with self.subTest(path=str(path.relative_to(ROOT))):
                check_path(path)

    def test_cli_check_cassette_ok(self):
        cassette = ROOT / "examples" / "fixtures" / "cassette"
        proc = subprocess.run(
            [sys.executable, "-m", "diptych.schema", "--check", str(cassette)],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=False,
            env=_cli_env(),
        )
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        self.assertIn("SCHEMA CHECK OK", proc.stdout)


class TestThinCorpusFailsSchema(unittest.TestCase):
    def test_each_thin_fixture_fails_check_path(self):
        from diptych.contract import ContractError
        from diptych.schema import SchemaError, check_path

        names = sorted(p.name for p in THIN.glob("*.json"))
        self.assertEqual(names, sorted(THIN_NAMES))
        for name in THIN_NAMES:
            path = THIN / name
            with self.subTest(fixture=name):
                with self.assertRaises((SchemaError, ContractError)):
                    check_path(path)

    def test_cli_check_thin_nonzero(self):
        proc = subprocess.run(
            [sys.executable, "-m", "diptych.schema", "--check", str(THIN)],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=False,
            env=_cli_env(),
        )
        self.assertNotEqual(proc.returncode, 0)
        combined = proc.stderr + proc.stdout
        self.assertIn("SCHEMA CHECK FAIL", combined)


class TestRejectedScoreKeysInSchema(unittest.TestCase):
    def test_schema_forbids_auroc_and_hardcoded_pass(self):
        from diptych.schema import REJECTED_TOP_LEVEL_KEYS, build_json_schema

        schema = build_json_schema()
        forbidden = {item["required"][0] for item in schema["not"]["anyOf"]}
        for key in ("auroc", "AUROC", "lab_auroc", "model_grade", "hardcoded_pass"):
            self.assertIn(key, forbidden)
            self.assertIn(key, REJECTED_TOP_LEVEL_KEYS)


class TestDumpSchemaCli(unittest.TestCase):
    def test_dump_schema_stdout(self):
        proc = subprocess.run(
            [sys.executable, "-m", "diptych.schema", "--dump-schema", "-"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=False,
            env=_cli_env(),
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        doc = json.loads(proc.stdout)
        self.assertEqual(doc["properties"]["diptych_schema"]["const"], "0.2")
        from diptych import OPERATORS

        self.assertEqual(doc["properties"]["operator"]["enum"], list(OPERATORS))


if __name__ == "__main__":
    unittest.main()
