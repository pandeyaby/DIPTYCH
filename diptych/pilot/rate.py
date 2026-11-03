"""Rating tool for human raters of an adjudication packet.

    python3 -m diptych.pilot.rate --packet PATH --rater NAME

Walks the packet's items in its own order, one at a time, and appends each
answer to ``answers.json`` in the packet (saved after every item, so it can
stop and resume). Records the time spent on each item. Shows nothing but
the packet's own files.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

CHOICES = {"v": "violates", "s": "satisfies", "u": "unsure"}
METHODS = {"c": "code reading", "e": "experiment", "b": "both"}


def _ask(prompt: str, valid: dict[str, str] | range) -> str:
    while True:
        a = input(prompt).strip().lower()
        if a == "q":
            raise KeyboardInterrupt
        if isinstance(valid, dict) and a in valid:
            return valid[a]
        if isinstance(valid, range) and a.isdigit() and int(a) in valid:
            return a


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="diptych.pilot.rate")
    p.add_argument("--packet", type=Path, required=True)
    p.add_argument("--rater", required=True)
    a = p.parse_args(argv)
    order = (a.packet / "ORDER.txt").read_text().split()
    path = a.packet / "answers.json"
    data = json.loads(path.read_text()) if path.exists() else {"rater": a.rater, "items": {}}
    if data.get("rater") != a.rater:
        print(f"answers.json belongs to {data.get('rater')!r}; refusing to mix raters.", file=sys.stderr)
        return 2
    todo = [i for i in order if i not in data["items"]]
    print(f"{len(order) - len(todo)} of {len(order)} items done. Type q at any prompt to stop.\n")
    try:
        for n, iid in enumerate(todo, len(order) - len(todo) + 1):
            d = a.packet / "items" / iid
            print("=" * 72)
            print(f"[{n}/{len(order)}] {iid}   directory: {d}")
            print((d / "REQUIREMENT.md").read_text())
            start = time.monotonic()
            input("Read controller.py (and TASK.md / the simulator as needed). Press Enter when ready to answer.")
            verdict = _ask("Verdict: [v]iolates / [s]atisfies / [u]nsure: ", CHOICES)
            conf = _ask("Confidence 1-5: ", range(1, 6))
            method = _ask("Method: [c]ode reading / [e]xperiment / [b]oth: ", METHODS)
            rationale = ""
            while len(rationale.split()) < 5:
                rationale = input("Rationale (the evidence that decided it, one line): ").strip()
            data["items"][iid] = {"verdict": verdict, "confidence": int(conf), "method": method,
                                  "rationale": rationale, "seconds": round(time.monotonic() - start),
                                  "answered_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
            path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    except (KeyboardInterrupt, EOFError):
        print("\nStopped; progress saved.")
    done = len(data["items"])
    print(f"\n{done} of {len(order)} items answered. {'All done: send answers.json.' if done == len(order) else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
