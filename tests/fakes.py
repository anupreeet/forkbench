"""In-memory stand-in for the Runta async SDK: same method names, no network, no cost.

It models just enough behaviour to test forkbench's logic: runtimes, checkpoints that copy a
runtime's state, a task container, an oracle that flips the reward, markers, and egress policy.
"""

from __future__ import annotations

import itertools
import time
from dataclasses import dataclass, field
from typing import Any

_ids = itertools.count(1)


@dataclass
class Result:
    exit_code: int = 0
    stdout_text: str = ""
    stderr_text: str = ""
    duration: float = 0.01


@dataclass
class State:
    files: dict[str, str] = field(default_factory=dict)
    image_pulled: bool = False
    container: str = "absent"  # absent | running | stopped
    solved: bool = False


class FakeFiles:
    def __init__(self, rt: FakeRuntime) -> None:
        self.rt = rt

    async def write(self, path: str, content: bytes | str, executable: bool = False) -> None:
        self.rt.state.files[path] = "<bytes>"


class FakeEgress:
    def __init__(self, rt: FakeRuntime) -> None:
        self.rt = rt

    async def set_policy(self, policy: Any) -> None:
        # Optionally model a propagation delay: the new policy applies after N more probes.
        if self.rt.client.policy_delay_probes:
            self.rt.pending = (policy, self.rt.client.policy_delay_probes)
        else:
            self.rt.policy = policy


@dataclass
class CheckpointInfo:
    id: str
    display_name: str
    state: str = "ready"


class FakeRuntimeCheckpoints:
    def __init__(self, rt: FakeRuntime) -> None:
        self.rt = rt

    async def create(self, name: str | None = None, **_: Any) -> CheckpointInfo:
        self.rt.client.calls.append("checkpoint")
        ck = CheckpointInfo(f"ck-{next(_ids)}", name or "ck")
        self.rt.client.saved[ck.id] = (ck, _copy(self.rt.state))
        return ck


def _copy(s: State) -> State:
    return State(dict(s.files), s.image_pulled, s.container, s.solved)


class FakeRuntime:
    def __init__(self, client: FakeClient, name: str, state: State, policy: Any = None) -> None:
        self.client = client
        self.id = f"rt-{next(_ids)}"
        self.name = name
        self.state = state
        self.policy = policy
        self.pending: tuple[Any, int] | None = None
        self.deleted = False
        self.files = FakeFiles(self)
        self.egress = FakeEgress(self)
        self.checkpoints = FakeRuntimeCheckpoints(self)

    def _tick_pending(self) -> None:
        if self.pending:
            policy, left = self.pending
            if left <= 1:
                self.policy, self.pending = policy, None
            else:
                self.pending = (policy, left - 1)

    def _blocked(self) -> bool:
        mode = str(getattr(self.policy, "mode", "")).lower()
        return "allow" in mode and not getattr(self.policy, "allowed_hosts", None)

    async def exec(self, command: Any, timeout: float | None = None) -> Result:
        cmd = command[-1] if isinstance(command, list) else str(command)
        self.client.calls.append(f"exec:{cmd[:40]}")
        for needle, err in self.client.fail_exec.items():
            if needle in cmd:
                return Result(1, "", err)
        st = self.state
        if "docker pull" in cmd:
            st.image_pulled = True
            return Result()
        if "docker create" in cmd:
            st.container = "running"
            return Result()
        if "docker inspect -f" in cmd:
            if st.container == "absent":
                return Result(1, "", "No such object: task")
            return Result(0, "true" if st.container == "running" else "false")
        if "docker start" in cmd:
            st.container = "running"
            return Result()
        if "solve.sh" in cmd:
            st.solved = True
            return Result()
        if "test-output.txt" in cmd and "tail" in cmd:
            if self.client.verifier_crash:
                return Result(0, "curl: (56) Failure when receiving data\nuv: command not found")
            word = "passed" if st.solved else "failed"
            return Result(0, f"======================== 6 {word} in 0.12s ========================")
        if "reward.txt" in cmd and "cat" in cmd:
            return Result(0, "1" if st.solved else "0")
        if "date +%s.%N" in cmd:
            return Result(0, f"{time.time():.6f}")
        if "boot_id" in cmd:
            return Result(0, f"boot-{self.client.boot}\nmid\nrunta\nuuid-{next(_ids)}")
        if "fb-leak-marker" in cmd and cmd.startswith("cat"):
            return Result(0, st.files.get("/root/fb-leak-marker", "ABSENT"))
        if "fb-leak-marker" in cmd and "docker exec" in cmd and "cat" in cmd:
            return Result(0, st.files.get("/tmp/fb-leak-marker", "ABSENT"))
        if "fb-leak-marker" in cmd and cmd.startswith("echo"):
            st.files["/root/fb-leak-marker"] = cmd.split()[1]
            return Result()
        if "fb-leak-marker" in cmd and "echo" in cmd:
            st.files["/tmp/fb-leak-marker"] = "marker"
            return Result()
        if "curl" in cmd and "pypi.org" in cmd:
            self._tick_pending()
            return Result(0, "000 35" if self._blocked() else "200 0")
        if "/dev/tcp" in cmd:
            body = "" if self._blocked() else "HTTP/1.1 200 OK\r\n\r\n<h1>Example Domain</h1>"
            return Result(0, body)
        return Result()

    async def delete(self) -> None:
        self.client.calls.append("delete")
        if self.client.delete_failures > 0:
            self.client.delete_failures -= 1
            raise RuntimeError("simulated network failure during delete")
        self.deleted = True


class FakeRuntimes:
    def __init__(self, client: FakeClient) -> None:
        self.client = client

    async def create(
        self,
        name: str | None = None,
        *,
        checkpoint_id: str | None = None,
        egress_policy: Any = None,
        **_: Any,
    ) -> FakeRuntime:
        self.client.calls.append("restore" if checkpoint_id else "create")
        if self.client.fail_create:
            raise RuntimeError("simulated create failure")
        state = _copy(self.client.saved[checkpoint_id][1]) if checkpoint_id else State()
        if checkpoint_id and self.client.restored_container_stopped:
            state.container = "stopped"
        rt = FakeRuntime(self.client, name or "rt", state, egress_policy)
        self.client.runtimes_made.append(rt)
        return rt

    async def list(self, status: Any = None) -> list[FakeRuntime]:
        return [r for r in self.client.runtimes_made if not r.deleted]


class FakeCheckpoints:
    def __init__(self, client: FakeClient) -> None:
        self.client = client

    async def delete(self, id: str) -> None:  # noqa: A002 - mirrors the SDK signature
        self.client.calls.append("delete_checkpoint")
        self.client.saved.pop(id, None)

    async def list(self) -> list[CheckpointInfo]:
        return [ck for ck, _ in self.client.saved.values()]


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.saved: dict[str, tuple[CheckpointInfo, State]] = {}
        self.runtimes_made: list[FakeRuntime] = []
        self.fail_exec: dict[str, str] = {}
        self.fail_create = False
        self.restored_container_stopped = False
        self.policy_delay_probes = 0
        self.verifier_crash = False
        self.delete_failures = 0
        self.boot = 1
        self.runtimes = FakeRuntimes(self)
        self.checkpoints = FakeCheckpoints(self)

    def alive(self) -> list[FakeRuntime]:
        return [r for r in self.runtimes_made if not r.deleted]
