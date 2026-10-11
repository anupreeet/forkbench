"""Reset strategies: how each trial gets a fresh, ready task environment.

scratch     new runtime -> docker pull -> start container           (what Harbor's Runta mode does today)
checkpoint  once: build that environment, checkpoint it, delete the builder
            per trial: new runtime FROM the checkpoint -> make sure the container runs
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from forkbench.envsetup import ensure_task_env, start_task_env
from forkbench.sandbox import Gateway, Sandbox
from forkbench.taskspec import TaskSpec


@dataclass
class Acquired:
    sandbox: Sandbox
    timings: dict[str, float] = field(default_factory=dict)
    notes: dict[str, Any] = field(default_factory=dict)


class Strategy(Protocol):
    name: str

    async def prepare(self, task: TaskSpec, run_id: str) -> dict[str, float]: ...
    async def acquire(
        self, task: TaskSpec, trial_name: str, egress_policy: Any = None
    ) -> Acquired: ...
    async def teardown(self) -> None: ...


class Scratch:
    name = "scratch"

    def __init__(self, gw: Gateway, setup_timeout_s: float) -> None:
        self._gw = gw
        self._setup_timeout_s = setup_timeout_s

    async def prepare(self, task: TaskSpec, run_id: str) -> dict[str, float]:
        return {}

    async def acquire(self, task: TaskSpec, trial_name: str, egress_policy: Any = None) -> Acquired:
        created = await self._gw.create(trial_name)
        sb: Sandbox = created.value
        try:
            env = await start_task_env(sb, task.docker_image, self._setup_timeout_s)
            if egress_policy is not None:  # setup needs the network (image pull); restrict after
                await sb.runtime.egress.set_policy(egress_policy)
        except BaseException:
            await self._gw.discard(sb)  # don't leave a half-built runtime billing until run end
            raise
        return Acquired(
            sb,
            {"provision_s": created.seconds, "pull_s": env.pull_s, "container_s": env.container_s},
        )

    async def teardown(self) -> None:
        return None


class Checkpoint:
    name = "checkpoint"

    def __init__(self, gw: Gateway, setup_timeout_s: float) -> None:
        self._gw = gw
        self._setup_timeout_s = setup_timeout_s
        self._checkpoint_id: str | None = None

    async def prepare(self, task: TaskSpec, run_id: str) -> dict[str, float]:
        """Build the 'golden' environment once and checkpoint it. Returned timings are one-off costs."""
        builder = await self._gw.create(f"{run_id}-{task.name}-golden")
        sb: Sandbox = builder.value
        try:
            env = await start_task_env(sb, task.docker_image, self._setup_timeout_s)
            ck = await self._gw.checkpoint(sb, f"{run_id}-{task.name}-ckpt")
        except BaseException:
            await self._gw.discard(sb)
            raise
        self._checkpoint_id = ck.value
        # Delete the builder now: restores must not depend on it staying alive (and it costs money).
        delete_s = await self._gw.delete(sb)
        return {
            "golden_provision_s": builder.seconds,
            "golden_pull_s": env.pull_s,
            "golden_container_s": env.container_s,
            "checkpoint_s": ck.seconds,
            "golden_delete_s": delete_s,
        }

    async def acquire(self, task: TaskSpec, trial_name: str, egress_policy: Any = None) -> Acquired:
        if self._checkpoint_id is None:
            raise RuntimeError("Checkpoint.prepare() must run before acquire()")
        restored = await self._gw.restore(
            self._checkpoint_id, trial_name, egress_policy=egress_policy
        )
        sb: Sandbox = restored.value
        try:
            env = await ensure_task_env(sb)
        except BaseException:
            await self._gw.discard(sb)
            raise
        return Acquired(
            sb,
            {"provision_s": restored.seconds, "pull_s": 0.0, "container_s": env.container_s},
            {"container_restarted": env.restarted},
        )

    async def teardown(self) -> None:
        if self._checkpoint_id:
            await self._gw.delete_checkpoint(self._checkpoint_id)
            self._checkpoint_id = None
