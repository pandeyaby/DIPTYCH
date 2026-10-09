"""Registry of pilot tasks: what differs between them, in one place."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

HERE = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Task:
    name: str
    root: Path                     # TASK.md, controls/, plant, simulator
    workspace: dict[str, str]      # file in agent workspace -> source file under root
    sim_hint: str                  # how the agent prompt describes sim.py
    results: str                   # results file name under diptych/pilot/

    @property
    def text(self) -> str:
        return (self.root / "TASK.md").read_text(encoding="utf-8")

    @property
    def controls(self) -> Path:
        return self.root / "controls"

    def keys(self) -> tuple[str, ...]:
        """Requirement keys, in report order."""
        if self.name == "concurrency":
            from diptych import OPERATORS
            return tuple(OPERATORS)
        from diptych.pilot.congestion.probes import KEYS
        return KEYS

    def evaluate(self) -> Callable[[str], dict[str, Any]]:
        if self.name == "concurrency":
            from diptych.pilot.probes import evaluate
        else:
            from diptych.pilot.congestion.probes import evaluate
        return evaluate

    def violated_key(self, stem: str) -> str:
        """Requirement key a violating control named ``stem`` targets."""
        return stem.upper() if self.name == "concurrency" else stem.replace("_", "-")

    def artifact_dir(self, condition: str, agent: str, model: str) -> Path:
        base = HERE / "artifacts"
        if self.name == "concurrency":
            if condition == "oneshot" and agent == "claude":
                return base / model  # original layout of the first batch
            return base / f"{condition}-{agent}" / model
        return base / self.name / f"{condition}-{agent}" / model


TASKS: dict[str, Task] = {
    "concurrency": Task(
        name="concurrency", root=HERE,
        workspace={"TASK.md": "TASK.md", "plant.py": "plant.py", "sim.py": "agent_sim.py"},
        sim_hint=("You can run `python3 sim.py controller.py` (or `--scenario NAME --trace`) to run "
                  "your controller in closed loop against the service plant"),
        results="results.json",
    ),
    "congestion": Task(
        name="congestion", root=HERE / "congestion",
        workspace={"TASK.md": "TASK.md", "plant.py": "plant.py", "reference.py": "reference.py",
                   "sim.py": "agent_sim.py"},
        sim_hint=("You can run `python3 sim.py controller.py` (add `--trace` for tick-by-tick output) "
                  "to run your controller on the simulated link, alone and beside a standard flow "
                  "(reference.py is the standard algorithm)"),
        results="results_congestion.json",
    ),
}
