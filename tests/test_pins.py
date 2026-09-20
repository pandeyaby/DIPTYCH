"""Tests for diptych.pins — parse + mismatch (no invented scores)."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

from diptych.pins import (  # noqa: E402
    EXIT_MISMATCH,
    EXIT_OK,
    EXIT_PARSE,
    check_paths,
    cross_check,
    main,
    parse_paper_short_pins,
    parse_pins_md,
    parse_readme_pins,
    validate_sha_pair,
)

PINS_OK = """# Adapter pins

| Adapter | Pin | Repo |
|---------|-----|------|
| ZeroDay | `fb5b39daf88e37521aaee8526ae9d286cf74f341` (**fb5b39da**) | https://github.com/pandeyaby/ZERODAY |
| AOMB | `667e47538ae5b9c504187b7a73220d22aa8fb96f` (**667e475**) | https://github.com/pandeyaby/AOMB |
"""

README_OK = """## Adapter pins

| Adapter | Short SHA | Full |
|---------|-----------|------|
| **ZeroDay** | `fb5b39da` | `fb5b39daf88e37521aaee8526ae9d286cf74f341` |
| **AOMB** | `667e475` | `667e47538ae5b9c504187b7a73220d22aa8fb96f` |
"""

PAPER_OK = """%% Adapter pins (read-only): ZeroDay@fb5b39da · AOMB@667e475
\\documentclass{article}
\\begin{document}
ZeroDay@\\texttt{fb5b39da} and AOMB@\\texttt{667e475}.
\\end{document}
"""


class TestValidateShaPair(unittest.TestCase):
    def test_ok(self):
        self.assertIsNone(
            validate_sha_pair(
                "fb5b39daf88e37521aaee8526ae9d286cf74f341", "fb5b39da"
            )
        )

    def test_short_not_prefix(self):
        self.assertIsNotNone(
            validate_sha_pair(
                "fb5b39daf88e37521aaee8526ae9d286cf74f341", "deadbeef"
            )
        )

    def test_bad_full_length(self):
        self.assertIsNotNone(validate_sha_pair("abc", "abc"))


class TestParsePinsMd(unittest.TestCase):
    def test_success(self):
        pins = parse_pins_md(PINS_OK)
        self.assertEqual(pins.zeroday.short, "fb5b39da")
        self.assertEqual(pins.aomb.short, "667e475")
        self.assertTrue(pins.zeroday.full.startswith(pins.zeroday.short))

    def test_missing_row(self):
        bad = "| ZeroDay | `fb5b39daf88e37521aaee8526ae9d286cf74f341` (**fb5b39da**) | x |\n"
        with self.assertRaises(ValueError):
            parse_pins_md(bad)

    def test_short_mismatch_in_row(self):
        bad = PINS_OK.replace("(**fb5b39da**)", "(**00000000**)")
        with self.assertRaises(ValueError):
            parse_pins_md(bad)


class TestParseReadmeAndPaper(unittest.TestCase):
    def test_readme(self):
        pins = parse_readme_pins(README_OK)
        self.assertEqual(pins.aomb.full[:7], "667e475")

    def test_paper_header(self):
        shorts = parse_paper_short_pins(PAPER_OK)
        self.assertEqual(shorts["zeroday"], "fb5b39da")
        self.assertEqual(shorts["aomb"], "667e475")


class TestCrossCheckMismatch(unittest.TestCase):
    def test_readme_full_mismatch(self):
        pins = parse_pins_md(PINS_OK)
        readme = parse_readme_pins(
            README_OK.replace(
                "fb5b39daf88e37521aaee8526ae9d286cf74f341",
                "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            ).replace("fb5b39da", "aaaaaaaa")
        )
        errs = cross_check(pins, readme=readme, paper_shorts=None, package_pins=None)
        self.assertTrue(any("README ZeroDay" in e for e in errs))

    def test_paper_short_mismatch(self):
        pins = parse_pins_md(PINS_OK)
        paper = parse_paper_short_pins(PAPER_OK.replace("fb5b39da", "deadbeef"))
        errs = cross_check(pins, readme=None, paper_shorts=paper, package_pins=None)
        self.assertTrue(any("paper ZeroDay" in e for e in errs))


class TestCheckPathsRepo(unittest.TestCase):
    def test_repo_files_ok(self):
        code, messages, pins = check_paths()
        self.assertEqual(code, EXIT_OK, messages)
        self.assertIsNotNone(pins)
        self.assertEqual(pins.zeroday.short, "fb5b39da")
        self.assertEqual(pins.aomb.short, "667e475")
        self.assertTrue(any(m.startswith("OK:") for m in messages))

    def test_temp_mismatch_exit_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            (t / "PINS.md").write_text(PINS_OK, encoding="utf-8")
            bad_readme = README_OK.replace("667e475", "0000000").replace(
                "667e47538ae5b9c504187b7a73220d22aa8fb96f",
                "0000000000000000000000000000000000000000",
            )
            (t / "README.md").write_text(bad_readme, encoding="utf-8")
            (t / "paper.tex").write_text(PAPER_OK, encoding="utf-8")
            code, messages, _ = check_paths(
                pins_path=t / "PINS.md",
                readme_path=t / "README.md",
                paper_path=t / "paper.tex",
                check_package=False,
            )
            self.assertEqual(code, EXIT_MISMATCH)
            self.assertTrue(messages)

    def test_temp_parse_failure_exit_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            (t / "PINS.md").write_text("# no rows\n", encoding="utf-8")
            (t / "README.md").write_text(README_OK, encoding="utf-8")
            (t / "paper.tex").write_text(PAPER_OK, encoding="utf-8")
            code, messages, _ = check_paths(
                pins_path=t / "PINS.md",
                readme_path=t / "README.md",
                paper_path=t / "paper.tex",
                check_package=False,
            )
            self.assertEqual(code, EXIT_PARSE)
            self.assertTrue(any("PINS parse failed" in m for m in messages))


class TestCLI(unittest.TestCase):
    def test_main_check_ok(self):
        self.assertEqual(main(["--check"]), EXIT_OK)

    def test_python_m_check(self):
        env = dict(**{k: v for k, v in __import__("os").environ.items()})
        env["PYTHONPATH"] = str(ROOT)
        proc = subprocess.run(
            [sys.executable, "-m", "diptych.pins", "--check"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(proc.returncode, EXIT_OK, proc.stderr)
        self.assertIn("OK:", proc.stdout)

    def test_requires_check_flag(self):
        # argparse error → SystemExit 2
        with self.assertRaises(SystemExit) as ctx:
            main([])
        self.assertNotEqual(ctx.exception.code, 0)


if __name__ == "__main__":
    unittest.main()
