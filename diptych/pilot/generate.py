"""Generate pilot artifacts: ask models to write a controller from TASK.md.

Each sample is one headless ``claude -p`` call with tools disabled, so the
model writes the controller from the specification alone (no execution, no
tests). The reply's first ``python`` code block is saved verbatim as
``artifacts/<model>/<k>.py``; ``artifacts/manifest.json`` records model, sample
index, cost, and whether a code block was found. Generated code is kept in the
repository so the study is reproducible without re-querying models.

    python -m diptych.pilot.generate --models claude-haiku-4-5-20251001 --samples 5
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ARTIFACTS = HERE / "artifacts"
DEFAULT_MODELS = ("claude-haiku-4-5-20251001", "claude-sonnet-5", "claude-opus-5-5", "claude-fable-5-1")

PROMPT = (
    "You are implementing the following task. Reply with the complete Python module "
    "in a single ```python code block and nothing else.\n\n"
)

_CODE = re.compile(r"```python\s*\n(.*?)```", re.S)


def generate_one(model: str, k: int, timeout: int = 900) -> dict:
    task = (HERE / "TASK.md").read_text(encoding="utf-8")
    out_dir = ARTIFACTS / model
    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as cwd:
        proc = subprocess.run(
            ["claude", "-p", PROMPT + task, "--model", model, "--output-format", "json",
             "--tools", "", "--no-session-persistence"],
            cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False,
        )
    rec = {"model": model, "sample": k, "returncode": proc.returncode}
    try:
        reply = json.loads(proc.stdout)
    except json.JSONDecodeError:
        rec["error"] = (proc.stderr or proc.stdout)[-500:]
        return rec
    rec["cost_usd"] = reply.get("total_cost_usd")
    text = reply.get("result") or ""
    m = _CODE.search(text)
    rec["code_block"] = bool(m)
    if m:
        path = out_dir / f"{k}.py"
        path.write_text(m.group(1), encoding="utf-8")
        rec["path"] = str(path.relative_to(HERE))
    else:
        rec["error"] = "no python code block in reply"
    return rec


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="diptych.pilot.generate")
    p.add_argument("--models", nargs="+", default=list(DEFAULT_MODELS))
    p.add_argument("--samples", type=int, default=5)
    p.add_argument("--jobs", type=int, default=6)
    args = p.parse_args(argv)
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    manifest_path = ARTIFACTS / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else []
    done = {(r["model"], r["sample"]) for r in manifest if r.get("code_block")}
    todo = [(m, k) for m in args.models for k in range(args.samples) if (m, k) not in done]
    with cf.ThreadPoolExecutor(max_workers=args.jobs) as ex:
        for rec in ex.map(lambda mk: generate_one(*mk), todo):
            manifest = [r for r in manifest if (r["model"], r["sample"]) != (rec["model"], rec["sample"])]
            manifest.append(rec)
            manifest.sort(key=lambda r: (r["model"], r["sample"]))
            manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
            print(json.dumps(rec), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
