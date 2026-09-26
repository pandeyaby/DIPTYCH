"""Subprocess entry point: run one artifact in a fresh interpreter.

Modes:
  trace     closed-loop steady run; prints the decision sequence
  snapshot  run to tick SNAPSHOT_AT, print the snapshot blob
  resume    read a snapshot from stdin, restore, continue to the end
  evaluate  full pilot evaluation (diptych.pilot.probes.evaluate)
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys

SNAPSHOT_AT = 120


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="diptych.pilot.worker")
    p.add_argument("--artifact", required=True)
    p.add_argument("--mode", choices=("trace", "snapshot", "resume", "evaluate"), required=True)
    args = p.parse_args(argv)
    random.seed(int(os.environ.get("PILOT_GLOBAL_SEED", "0")))

    from diptych.pilot.plant import CONFIG, SCENARIOS, Plant
    from diptych.pilot.probes import (
        Counted, _decisions, closed_loop_trace, evaluate, load_controller_class, run_closed_loop,
    )

    if args.mode == "evaluate":
        print(json.dumps(evaluate(args.artifact), default=repr))
        return 0
    cls = load_controller_class(args.artifact)
    plant = Plant(SCENARIOS["steady"])
    if args.mode == "trace":
        print(json.dumps({"decisions": _decisions(closed_loop_trace(cls, "steady"))}))
    elif args.mode == "snapshot":
        c = cls(dict(CONFIG))
        prev = run_closed_loop(Counted(c, [0]), plant, 0, SNAPSHOT_AT, CONFIG["initial_limit"])
        blob = c.snapshot()
        if not isinstance(blob, str):
            raise TypeError("snapshot() did not return str")
        print(json.dumps({"at": SNAPSHOT_AT, "blob": blob, "prev_limit": prev}))
    else:
        snap = json.loads(sys.stdin.read())
        c = cls.restore(snap["blob"])
        trace: list[dict] = []
        run_closed_loop(Counted(c, [0]), plant, snap["at"], plant.scenario.length,
                        snap["prev_limit"], trace)
        print(json.dumps({"decisions": _decisions(trace)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
