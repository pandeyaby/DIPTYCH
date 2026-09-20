"""Adapter pin checker — parse adapters/PINS.md and cross-check docs.

Verifies ZeroDay / AOMB full + short SHAs against README and the paper header.
Does not invent matrix greens, scores, or product SHAs. Does not edit ZeroDay
or AOMB product trees.

Stranger path::

    python -m diptych.pins --check
    diptych-pins --check
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PINS = ROOT / "adapters" / "PINS.md"
DEFAULT_README = ROOT / "README.md"
DEFAULT_PAPER = ROOT / "paper" / "one-trace-is-not-enough.tex"

# Full git SHA (40 hex) and short display forms used in docs.
_FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_SHORT_SHA_RE = re.compile(r"^[0-9a-f]{7,12}$")

# PINS.md table row: | ZeroDay | `FULL` (**short**) | url |
_PINS_ROW_RE = re.compile(
    r"^\|\s*(ZeroDay|AOMB)\s*\|\s*`([0-9a-f]{40})`\s*\(\*\*([0-9a-f]{7,12})\*\*\)\s*\|",
    re.IGNORECASE | re.MULTILINE,
)

# README table: | **ZeroDay** | `short` | `full` |
_README_ROW_RE = re.compile(
    r"^\|\s*\*\*(ZeroDay|AOMB)\*\*\s*\|\s*`([0-9a-f]{7,12})`\s*\|\s*`([0-9a-f]{40})`\s*\|",
    re.IGNORECASE | re.MULTILINE,
)

# Paper header / body short pins: ZeroDay@fb5b39da · AOMB@667e475
_PAPER_ZD_RE = re.compile(r"ZeroDay@`?([0-9a-f]{7,12})`?", re.IGNORECASE)
_PAPER_AOMB_RE = re.compile(r"AOMB@`?([0-9a-f]{7,12})`?", re.IGNORECASE)

EXIT_OK = 0
EXIT_MISMATCH = 1
EXIT_PARSE = 2


@dataclass(frozen=True)
class AdapterPin:
    name: str  # ZeroDay | AOMB
    full: str
    short: str

    def key(self) -> str:
        return self.name.lower()


@dataclass(frozen=True)
class PinSet:
    zeroday: AdapterPin
    aomb: AdapterPin

    def by_name(self) -> dict[str, AdapterPin]:
        return {"zeroday": self.zeroday, "aomb": self.aomb}


def validate_sha_pair(full: str, short: str) -> Optional[str]:
    """Return an error message if full/short are malformed or inconsistent."""
    if not _FULL_SHA_RE.match(full):
        return f"full SHA must be 40 lowercase hex, got {full!r}"
    if not _SHORT_SHA_RE.match(short):
        return f"short SHA must be 7–12 lowercase hex, got {short!r}"
    if not full.startswith(short):
        return f"short {short!r} is not a prefix of full {full!r}"
    return None


def parse_pins_md(text: str) -> PinSet:
    """Parse ZeroDay/AOMB rows from adapters/PINS.md."""
    found: dict[str, AdapterPin] = {}
    for m in _PINS_ROW_RE.finditer(text):
        name, full, short = m.group(1), m.group(2), m.group(3)
        err = validate_sha_pair(full, short)
        if err:
            raise ValueError(f"{name}: {err}")
        pin = AdapterPin(name=name, full=full, short=short)
        found[pin.key()] = pin
    if "zeroday" not in found or "aomb" not in found:
        missing = [n for n in ("zeroday", "aomb") if n not in found]
        raise ValueError(f"PINS.md missing adapter row(s): {', '.join(missing)}")
    return PinSet(zeroday=found["zeroday"], aomb=found["aomb"])


def parse_readme_pins(text: str) -> PinSet:
    """Parse ZeroDay/AOMB rows from README adapter-pins table."""
    found: dict[str, AdapterPin] = {}
    for m in _README_ROW_RE.finditer(text):
        name, short, full = m.group(1), m.group(2), m.group(3)
        err = validate_sha_pair(full, short)
        if err:
            raise ValueError(f"README {name}: {err}")
        pin = AdapterPin(name=name, full=full, short=short)
        found[pin.key()] = pin
    if "zeroday" not in found or "aomb" not in found:
        missing = [n for n in ("zeroday", "aomb") if n not in found]
        raise ValueError(f"README missing adapter row(s): {', '.join(missing)}")
    return PinSet(zeroday=found["zeroday"], aomb=found["aomb"])


def parse_paper_short_pins(text: str) -> dict[str, str]:
    """Extract short pins from paper header/body (ZeroDay@… / AOMB@…)."""
    # Prefer the header STATUS/Adapter pins line (first occurrence of each).
    zd = _PAPER_ZD_RE.search(text)
    aomb = _PAPER_AOMB_RE.search(text)
    if zd is None or aomb is None:
        missing = []
        if zd is None:
            missing.append("ZeroDay@")
        if aomb is None:
            missing.append("AOMB@")
        raise ValueError(f"paper missing short pin marker(s): {', '.join(missing)}")
    out = {"zeroday": zd.group(1).lower(), "aomb": aomb.group(1).lower()}
    for key, short in out.items():
        if not _SHORT_SHA_RE.match(short):
            raise ValueError(f"paper {key} short SHA malformed: {short!r}")
    return out


def _mismatch(label: str, expected: str, got: str) -> str:
    return f"{label}: expected {expected}, got {got}"


def cross_check(
    pins: PinSet,
    *,
    readme: Optional[PinSet] = None,
    paper_shorts: Optional[dict[str, str]] = None,
    package_pins: Optional[dict[str, str]] = None,
) -> list[str]:
    """Return a list of human-readable mismatch errors (empty = OK)."""
    errors: list[str] = []
    if readme is not None:
        for key, pin in pins.by_name().items():
            other = readme.by_name()[key]
            if pin.full != other.full:
                errors.append(_mismatch(f"README {pin.name} full", pin.full, other.full))
            if pin.short != other.short:
                errors.append(
                    _mismatch(f"README {pin.name} short", pin.short, other.short)
                )
    if paper_shorts is not None:
        for key, pin in pins.by_name().items():
            got = paper_shorts.get(key)
            if got != pin.short:
                errors.append(
                    _mismatch(f"paper {pin.name} short", pin.short, str(got))
                )
    if package_pins is not None:
        # Optional: diptych.ADAPTER_PINS full SHAs (already in package).
        mapping = {"zeroday": "zeroday", "aomb": "aomb"}
        for key, pin in pins.by_name().items():
            pkg_key = mapping[key]
            got = package_pins.get(pkg_key)
            if got is not None and got != pin.full:
                errors.append(
                    _mismatch(f"diptych.ADAPTER_PINS[{pkg_key!r}]", pin.full, got)
                )
    return errors


def check_paths(
    *,
    pins_path: Path = DEFAULT_PINS,
    readme_path: Path = DEFAULT_README,
    paper_path: Path = DEFAULT_PAPER,
    check_package: bool = True,
) -> tuple[int, list[str], PinSet | None]:
    """Load files, parse, cross-check. Returns (exit_code, messages, pins)."""
    messages: list[str] = []
    try:
        pins_text = pins_path.read_text(encoding="utf-8")
        pins = parse_pins_md(pins_text)
    except (OSError, ValueError) as exc:
        return EXIT_PARSE, [f"PINS parse failed: {exc}"], None

    try:
        readme = parse_readme_pins(readme_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return EXIT_PARSE, [f"README parse failed: {exc}"], pins

    try:
        paper_shorts = parse_paper_short_pins(paper_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return EXIT_PARSE, [f"paper parse failed: {exc}"], pins

    package_pins: Optional[dict[str, str]] = None
    if check_package:
        try:
            from diptych import ADAPTER_PINS

            package_pins = dict(ADAPTER_PINS)
        except Exception as exc:  # pragma: no cover
            messages.append(f"warning: could not import ADAPTER_PINS: {exc}")

    errors = cross_check(
        pins, readme=readme, paper_shorts=paper_shorts, package_pins=package_pins
    )
    if errors:
        return EXIT_MISMATCH, errors, pins
    messages.append(
        f"OK: ZeroDay@{pins.zeroday.short} / AOMB@{pins.aomb.short} "
        f"(PINS.md ↔ README ↔ paper header)"
    )
    return EXIT_OK, messages, pins


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Check ZeroDay/AOMB adapter pins in adapters/PINS.md against "
            "README + paper header. No invented scores or matrix greens."
        )
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Parse PINS.md and cross-check README + paper (required mode)",
    )
    parser.add_argument(
        "--pins",
        type=Path,
        default=DEFAULT_PINS,
        help=f"Path to PINS.md (default: {DEFAULT_PINS})",
    )
    parser.add_argument(
        "--readme",
        type=Path,
        default=DEFAULT_README,
        help=f"Path to README.md (default: {DEFAULT_README})",
    )
    parser.add_argument(
        "--paper",
        type=Path,
        default=DEFAULT_PAPER,
        help=f"Path to paper .tex (default: {DEFAULT_PAPER})",
    )
    parser.add_argument(
        "--no-package",
        action="store_true",
        help="Skip cross-check against diptych.ADAPTER_PINS",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    if not args.check:
        parser.error("pass --check (only supported mode)")

    code, messages, _pins = check_paths(
        pins_path=args.pins,
        readme_path=args.readme,
        paper_path=args.paper,
        check_package=not args.no_package,
    )
    stream = sys.stdout if code == EXIT_OK else sys.stderr
    for line in messages:
        print(line, file=stream)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
