"""Console-script entry points + package version alignment (#23).

Requires an editable/wheel install so ``importlib.metadata`` sees the
distribution (CI: ``pip install -e ".[dev]"``). Without install, resolution
tests are skipped; ``__version__`` vs pyproject still runs.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# name → "module:attr" as declared in pyproject [project.scripts]
EXPECTED_SCRIPTS: dict[str, str] = {
    "diptych": "diptych.smoke:main",
    "diptych-poc": "diptych.poc:main",
    "diptych-full8": "diptych.run_full8:main",
    "diptych-grade": "diptych.grade:main",
    "diptych-matrix": "diptych.matrix:main",
    "diptych-schema": "diptych.schema:main",
    "diptych-pins": "diptych.pins:main",
    "diptych-probe-tree": "diptych.probe_tree:main",
}


def _pyproject_version() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'(?m)^version\s*=\s*"([^"]+)"', text)
    if not m:
        raise AssertionError("version = \"...\" not found in pyproject.toml")
    return m.group(1)


def _console_scripts():
    from importlib.metadata import PackageNotFoundError, entry_points

    try:
        eps = entry_points()
    except PackageNotFoundError:  # pragma: no cover
        return None
    if hasattr(eps, "select"):
        return list(eps.select(group="console_scripts"))
    return list(eps.get("console_scripts", []))  # type: ignore[arg-type]


def _dist_installed() -> bool:
    try:
        from importlib.metadata import version

        version("diptych")
        return True
    except Exception:
        return False


class TestPackageVersion(unittest.TestCase):
    def test_dunder_version_matches_pyproject(self):
        from diptych import __version__

        self.assertEqual(__version__, _pyproject_version())
        self.assertEqual(__version__, "0.2.0")

    def test_metadata_version_matches_when_installed(self):
        if not _dist_installed():
            self.skipTest("diptych not installed (pip install -e .)")
        from importlib.metadata import version

        from diptych import __version__

        self.assertEqual(version("diptych"), __version__)
        self.assertEqual(version("diptych"), _pyproject_version())


class TestConsoleScripts(unittest.TestCase):
    def test_entry_points_resolve_and_load(self):
        if not _dist_installed():
            self.skipTest("diptych not installed (pip install -e .)")
        scripts = _console_scripts()
        self.assertIsNotNone(scripts)
        by_name = {ep.name: ep for ep in scripts}
        for name, target in EXPECTED_SCRIPTS.items():
            self.assertIn(name, by_name, f"missing console_scripts entry: {name}")
            ep = by_name[name]
            value = getattr(ep, "value", None) or f"{ep.module}:{ep.attr}"
            self.assertEqual(value, target, name)
            loaded = ep.load()
            self.assertTrue(callable(loaded), f"{name} loaded non-callable: {loaded!r}")

    def test_diptych_cli_smoke_help_and_quiet(self):
        if not _dist_installed():
            self.skipTest("diptych not installed (pip install -e .)")
        exe = shutil.which("diptych")
        if exe is None:
            # Editable install may put scripts outside current PATH; invoke via
            # the loaded entry point (same callable as the console script).
            from importlib.metadata import entry_points

            eps = entry_points()
            if hasattr(eps, "select"):
                matches = list(eps.select(group="console_scripts", name="diptych"))
            else:
                matches = [
                    e for e in eps.get("console_scripts", []) if e.name == "diptych"
                ]
            self.assertTrue(matches, "diptych entry point missing")
            main = matches[0].load()
            with self.assertRaises(SystemExit) as cm:
                main(["--help"])
            self.assertEqual(cm.exception.code, 0)
            rc = main(["smoke", "--quiet"])
            self.assertEqual(rc, 0)
            return

        help_proc = subprocess.run(
            [exe, "--help"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(help_proc.returncode, 0, help_proc.stderr)
        self.assertIn("smoke", help_proc.stdout.lower())

        smoke_proc = subprocess.run(
            [exe, "smoke", "--quiet"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(smoke_proc.returncode, 0, smoke_proc.stderr + smoke_proc.stdout)

    def test_python_m_diptych_still_works(self):
        """Keep ``python -m diptych`` after console_scripts land."""
        env = dict(os.environ)
        env["PYTHONPATH"] = str(ROOT)
        proc = subprocess.run(
            [sys.executable, "-m", "diptych", "--help"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("smoke", proc.stdout.lower())


if __name__ == "__main__":
    unittest.main()
