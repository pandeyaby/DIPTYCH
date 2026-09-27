"""Generate pilot artifacts: ask models or agents to write a controller from TASK.md.

Conditions (``--condition``) and agents (``--agent``):

* ``oneshot`` / ``claude`` — one headless ``claude -p`` turn with tools
  disabled; the reply's first ``python`` block is saved.
* ``agentic`` / ``claude`` — ``claude -p`` in a fresh workspace holding
  ``TASK.md``, ``plant.py`` and ``sim.py`` (a closed-loop runner); tools are
  Read/Write/Edit and ``python`` only, no web. The agent writes
  ``controller.py``, which is saved.
* ``oneshot`` / ``codex`` — ``codex exec`` in a read-only sandbox, instructed
  not to run commands; the reply's code block is saved (whether it ran
  commands anyway is recorded).
* ``agentic`` / ``codex`` — ``codex exec`` with a workspace-write sandbox in
  the same workspace as the Claude agent.

Agents never see the DIPTYCH probes, controls, or graders. Saved code goes
to ``artifacts/<condition-dir>/<model>/<k>.py`` and every sample is recorded
in ``artifacts/manifest.json`` (condition, agent, model, cost or tokens).

    python -m diptych.pilot.generate --condition agentic --agent claude --models claude-sonnet-5
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import fcntl
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ARTIFACTS = HERE / "artifacts"
DEFAULT_MODELS = {
    ("oneshot", "claude"): ("claude-haiku-4-5-20251001", "claude-sonnet-5", "claude-opus-5-5", "claude-fable-5-1"),
    ("agentic", "claude"): ("claude-sonnet-5", "claude-opus-5-5"),
    ("oneshot", "codex"): ("gpt-5.6-sol",),
    ("agentic", "codex"): ("gpt-5.6-sol",),
}

ONESHOT_PROMPT = (
    "You are implementing the following task. Reply with the complete Python module "
    "in a single ```python code block and nothing else.\n\n"
)
CODEX_ONESHOT_PROMPT = (
    "Do not run any commands or read any files. " + ONESHOT_PROMPT
)
AGENT_PROMPT = (
    "Implement the task in TASK.md in this directory. Write the final module to "
    "controller.py. You can run `python3 sim.py controller.py` (or `--scenario NAME "
    "--trace`) to run your controller in closed loop against the service plant, and "
    "you may write and run your own tests. Stop when controller.py meets every "
    "requirement in TASK.md."
)

_CODE = re.compile(r"```python\s*\n(.*?)```", re.S)


def _dir_for(condition: str, agent: str, model: str) -> Path:
    if condition == "oneshot" and agent == "claude":
        return ARTIFACTS / model  # original layout of the first pilot batch
    return ARTIFACTS / f"{condition}-{agent}" / model


def _workspace(cwd: Path) -> None:
    shutil.copy(HERE / "TASK.md", cwd / "TASK.md")
    shutil.copy(HERE / "plant.py", cwd / "plant.py")
    shutil.copy(HERE / "agent_sim.py", cwd / "sim.py")


def _save(rec: dict, code: str | None, out: Path) -> dict:
    if code:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(code, encoding="utf-8")
        rec["code_block"] = True
        rec["path"] = str(out.relative_to(HERE))
    else:
        rec["code_block"] = False
        rec.setdefault("error", "no controller produced")
    return rec


def _claude(condition: str, model: str, cwd: Path, timeout: int) -> tuple[dict, str | None]:
    if condition == "oneshot":
        args = ["claude", "-p", ONESHOT_PROMPT + (HERE / "TASK.md").read_text(), "--tools", ""]
    else:
        args = ["claude", "-p", AGENT_PROMPT, "--permission-mode", "acceptEdits",
                "--allowedTools", "Read,Write,Edit,Glob,Grep,Bash(python3:*),Bash(python:*)",
                "--disallowedTools", "WebFetch,WebSearch", "--max-turns", "60"]
    args += ["--model", model, "--output-format", "json", "--no-session-persistence"]
    proc = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False)
    rec: dict = {"returncode": proc.returncode}
    try:
        reply = json.loads(proc.stdout)
    except json.JSONDecodeError:
        rec["error"] = (proc.stderr or proc.stdout)[-500:]
        return rec, None
    rec["cost_usd"] = reply.get("total_cost_usd")
    rec["num_turns"] = reply.get("num_turns")
    if condition == "oneshot":
        m = _CODE.search(reply.get("result") or "")
        return rec, m.group(1) if m else None
    ctrl = cwd / "controller.py"
    return rec, ctrl.read_text() if ctrl.exists() else None


def _codex(condition: str, model: str, cwd: Path, timeout: int) -> tuple[dict, str | None]:
    sandbox = "read-only" if condition == "oneshot" else "workspace-write"
    prompt = (CODEX_ONESHOT_PROMPT + (HERE / "TASK.md").read_text()) if condition == "oneshot" else AGENT_PROMPT
    proc = subprocess.run(
        ["codex", "exec", "--json", "--sandbox", sandbox, "--skip-git-repo-check", "--ephemeral",
         "-m", model, "-C", str(cwd), prompt],
        capture_output=True, text=True, timeout=timeout, check=False,
    )
    rec: dict = {"returncode": proc.returncode, "usage": {}, "commands_run": 0}
    last_text = ""
    for line in proc.stdout.splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        item = ev.get("item") or {}
        if ev.get("type") == "item.completed" and item.get("type") == "agent_message":
            last_text = item.get("text") or last_text
        if item.get("type") == "command_execution" and ev.get("type") == "item.completed":
            rec["commands_run"] += 1
        if ev.get("type") == "turn.completed":
            for k, v in (ev.get("usage") or {}).items():
                rec["usage"][k] = rec["usage"].get(k, 0) + v
    if proc.returncode != 0 and not last_text:
        rec["error"] = proc.stderr[-500:]
    if condition == "oneshot":
        m = _CODE.search(last_text)
        return rec, m.group(1) if m else None
    ctrl = cwd / "controller.py"
    return rec, ctrl.read_text() if ctrl.exists() else None


def generate_one(condition: str, agent: str, model: str, k: int, timeout: int = 2400) -> dict:
    rec = {"condition": condition, "agent": agent, "model": model, "sample": k}
    with tempfile.TemporaryDirectory() as tmp:
        cwd = Path(tmp)
        if condition == "agentic":
            _workspace(cwd)
        try:
            fn = _claude if agent == "claude" else _codex
            extra, code = fn(condition, model, cwd, timeout)
        except subprocess.TimeoutExpired:
            extra, code = {"error": "timed out"}, None
    rec.update(extra)
    return _save(rec, code, _dir_for(condition, agent, model) / f"{k}.py")


def _key(r: dict) -> tuple:
    return (r.get("condition", "oneshot"), r.get("agent", "claude"), r["model"], r["sample"])


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="diptych.pilot.generate")
    p.add_argument("--condition", choices=("oneshot", "agentic"), default="oneshot")
    p.add_argument("--agent", choices=("claude", "codex"), default="claude")
    p.add_argument("--models", nargs="+", default=None)
    p.add_argument("--samples", type=int, default=5)
    p.add_argument("--jobs", type=int, default=5)
    args = p.parse_args(argv)
    models = args.models or list(DEFAULT_MODELS[(args.condition, args.agent)])
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    manifest_path = ARTIFACTS / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else []
    for r in manifest:  # first batch predates the condition/agent fields
        r.setdefault("condition", "oneshot")
        r.setdefault("agent", "claude")
    done = {_key(r) for r in manifest if r.get("code_block")}
    todo = [(args.condition, args.agent, m, k) for m in models for k in range(args.samples)
            if (args.condition, args.agent, m, k) not in done]
    with cf.ThreadPoolExecutor(max_workers=args.jobs) as ex:
        for rec in ex.map(lambda t: generate_one(*t), todo):
            _record(manifest_path, rec)
            print(json.dumps(rec), flush=True)
    return 0


def _record(manifest_path: Path, rec: dict) -> None:
    """Merge one record into the manifest under a lock (parallel generators)."""
    with open(manifest_path.with_suffix(".lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else []
        for r in manifest:
            r.setdefault("condition", "oneshot")
            r.setdefault("agent", "claude")
        manifest = [r for r in manifest if _key(r) != _key(rec)]
        manifest.append(rec)
        manifest.sort(key=_key)
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    sys.exit(main())
