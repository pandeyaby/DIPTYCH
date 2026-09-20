"""Lightweight checks for scripts/pack_artifact.sh (no TeX required)."""
from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "scripts" / "pack_artifact.sh"


class TestPackArtifact(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not PACK.is_file():
            raise unittest.SkipTest("pack_artifact.sh missing")
        if not (ROOT / "coverage" / "matrix.json").is_file():
            raise unittest.SkipTest("coverage/matrix.json missing")

    def test_pack_script_is_executable_and_shebang(self) -> None:
        text = PACK.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("#!/usr/bin/env bash"))
        self.assertTrue(PACK.stat().st_mode & 0o111, "pack_artifact.sh should be executable")

    def test_pack_creates_archive_with_required_members(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            proc = subprocess.run(
                ["bash", str(PACK), str(out)],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(
                proc.returncode,
                0,
                msg=f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}",
            )
            archives = list(out.glob("DIPTYCH-*.zip")) + list(out.glob("DIPTYCH-*.tar.gz"))
            self.assertEqual(len(archives), 1, f"expected one archive, got {archives}")
            archive = archives[0]
            self.assertGreater(archive.stat().st_size, 1000)

            required_suffixes = (
                "CODE_SHA.txt",
                "ARTIFACT_NOTES.txt",
                "coverage/matrix.json",
                "examples/poc/expected_matrix_snippet.json",
                "adapters/PINS.md",
                "paper/SUBMISSION.md",
                "paper/RQ_PROTOCOL.md",
                "paper/ANON.md",
                "paper/WITNESSES.md",
                "docs/adapters/WITNESSES.md",
                "docs/adapters/GATING.md",
                "scripts/run_poc.sh",
                "LICENSE",
                "CITATION.cff",
            )

            if archive.suffix == ".zip":
                with zipfile.ZipFile(archive) as zf:
                    names = zf.namelist()
                joined = "\n".join(names)
                for suf in required_suffixes:
                    self.assertTrue(
                        any(n.endswith(suf) for n in names),
                        msg=f"missing {suf} in zip\n{joined[:2000]}",
                    )
                # Must not vendor product trees
                self.assertFalse(any("/ZERODAY/" in n or n.endswith("/ZERODAY") for n in names))
                self.assertFalse(any("/AOMB/" in n or n.endswith("/AOMB") for n in names))
            else:
                # tar.gz fallback: just ensure extract lists key files
                import tarfile

                with tarfile.open(archive, "r:gz") as tf:
                    names = tf.getnames()
                for suf in required_suffixes:
                    self.assertTrue(
                        any(n.endswith(suf) for n in names),
                        msg=f"missing {suf} in tar",
                    )


if __name__ == "__main__":
    unittest.main()
