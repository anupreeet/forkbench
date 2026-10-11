"""Read a Harbor / Terminal-Bench task folder.

Layout: task.toml, instruction.md, environment/, solution/solve.sh, tests/test.sh.
Task code is untrusted: we only read these files locally; they execute inside Runta runtimes.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path


class TaskError(ValueError):
    pass


@dataclass(frozen=True)
class TaskSpec:
    name: str
    path: Path
    docker_image: str
    agent_timeout_s: float
    verifier_timeout_s: float

    @property
    def solution_dir(self) -> Path:
        return self.path / "solution"

    @property
    def tests_dir(self) -> Path:
        return self.path / "tests"


def load_task(path: Path) -> TaskSpec:
    path = path.resolve()
    toml_path = path / "task.toml"
    if not toml_path.is_file():
        raise TaskError(f"{path}: missing task.toml")
    data = tomllib.loads(toml_path.read_text())
    image = data.get("environment", {}).get("docker_image")
    if not image:
        # forkbench measures reset strategies for prebuilt task images; building is out of scope.
        raise TaskError(f"{path.name}: no [environment].docker_image (prebuilt image required)")
    for required in ("solution/solve.sh", "tests/test.sh"):
        if not (path / required).is_file():
            raise TaskError(f"{path.name}: missing {required}")
    return TaskSpec(
        name=path.name,
        path=path,
        docker_image=str(image),
        agent_timeout_s=float(data.get("agent", {}).get("timeout_sec", 900.0)),
        verifier_timeout_s=float(data.get("verifier", {}).get("timeout_sec", 900.0)),
    )
