#!/usr/bin/env python3
"""Refresh coverage/matrix.json diptych_core cells from fixture grades.

Thin stranger wrapper around ``python -m diptych.matrix --write``.

Adapter columns (zeroday / aomb) remain pin-documented — this script never
invents ZeroDay/AOMB greens from partial fixture trees.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from diptych.matrix import main

if __name__ == "__main__":
    # Default to --write unless caller already passed a mode flag.
    argv = sys.argv[1:]
    if not any(a in {"--check", "--write", "-h", "--help"} for a in argv):
        argv = ["--write", *argv]
    raise SystemExit(main(argv))
