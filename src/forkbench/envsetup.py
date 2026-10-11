"""Bring a task's Docker environment up inside a runtime.

Mirrors Harbor's Runta "docker" mode (harbor/src/harbor/environments/runta.py, v0.24.0):
`docker image inspect || docker pull` -> `docker create ... sh -c 'while true; do sleep 3600; done'`
-> `docker start`. So "scratch" timings reflect what Harbor users get today.
"""

from __future__ import annotations

import shlex
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from forkbench.sandbox import ExecOutcome, Sandbox, SandboxError

CONTAINER = "task"
KEEPALIVE = "while true; do sleep 3600; done"


@dataclass(frozen=True)
class EnvTimings:
    pull_s: float = 0.0
    container_s: float = 0.0
    restarted: bool = False  # True if a restored container was not running and we had to start it


async def start_task_env(sb: Sandbox, image: str, timeout_s: float) -> EnvTimings:
    img = shlex.quote(image)
    t0 = time.monotonic()
    await sb.sh(
        f"docker image inspect {img} >/dev/null 2>&1 || docker pull -q {img}",
        timeout=timeout_s,
        check=True,
    )
    t1 = time.monotonic()
    await sb.sh(
        f"docker rm -f {CONTAINER} >/dev/null 2>&1; "
        f"docker create --name {CONTAINER} {img} sh -c {shlex.quote(KEEPALIVE)} >/dev/null "
        f"&& docker start {CONTAINER} >/dev/null "
        f"&& docker exec {CONTAINER} mkdir -p /logs/verifier /logs/agent",
        timeout=120,
        check=True,
    )
    return EnvTimings(pull_s=t1 - t0, container_s=time.monotonic() - t1)


async def ensure_task_env(sb: Sandbox) -> EnvTimings:
    """After a restore: is the task container still running? If not, start it (and time that)."""
    t0 = time.monotonic()
    state = await sb.sh(f"docker inspect -f '{{{{.State.Running}}}}' {CONTAINER}", timeout=60)
    restarted = False
    if state.exit_code != 0:
        raise SandboxError(
            "ensure task env", f"container missing after restore: {state.stderr[-300:]}"
        )
    if state.stdout.strip() != "true":
        await sb.sh(f"docker start {CONTAINER} >/dev/null", timeout=120, check=True)
        restarted = True
    await sb.sh(f"docker exec {CONTAINER} true", timeout=60, check=True)
    return EnvTimings(container_s=time.monotonic() - t0, restarted=restarted)


async def ctr(
    sb: Sandbox, command: str, timeout: float = 300.0, check: bool = False
) -> ExecOutcome:
    """Run a command inside the task container."""
    return await sb.sh(
        f"docker exec {CONTAINER} bash -c {shlex.quote(command)}", timeout=timeout, check=check
    )


async def copy_into_container(sb: Sandbox, local_dir: Path, container_dir: str) -> None:
    staging = f"/tmp/fb-stage-{local_dir.name}-{uuid.uuid4().hex[:8]}"  # noqa: S108 - path in the remote sandbox
    await sb.put_dir(local_dir, staging)
    await sb.sh(
        f"docker exec {CONTAINER} mkdir -p {shlex.quote(container_dir)} && "
        f"docker cp {staging}/. {CONTAINER}:{shlex.quote(container_dir)} && rm -rf {staging}",
        timeout=120,
        check=True,
    )
