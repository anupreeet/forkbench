"""Thin, timed wrapper over the Runta async SDK.

`Gateway` creates/restores runtimes and checkpoints and remembers everything it created, so a single
`cleanup()` call (run in `finally`, shielded from Ctrl-C) deletes it all. Retries are left to the SDK,
which already retries transient failures (AsyncRunta(retry_max_attempts=16)); adding a second retry
layer here would multiply waits and hide real errors.
"""

from __future__ import annotations

import asyncio
import io
import shlex
import tarfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from forkbench.log import get_logger

log = get_logger("sandbox")

# Readiness polling interval. The SDK default (0.5 s) multiplied across parallel runs got us HTTP 429s;
# 1 s halves API load and adds at most ~1 s to measured waits, equally for every strategy.
POLL_S = 1.0


class SandboxError(RuntimeError):
    """A step failed. `step` names it; `detail` carries Runta's message (incl. request ids if any)."""

    def __init__(self, step: str, detail: str) -> None:
        super().__init__(f"{step}: {detail}")
        self.step = step
        self.detail = detail


@dataclass(frozen=True)
class ExecOutcome:
    exit_code: int
    stdout: str
    stderr: str
    # Measured on our side: request sent -> response received. (The SDK's CommandResult.duration
    # is also measured client-side, so it can't be used to split network vs server time.)
    wall_s: float


class RuntimeLike(Protocol):
    """The subset of runta.AsyncRuntime that forkbench uses (lets tests use a fake)."""

    id: str
    name: str

    async def exec(self, command: Any, timeout: float | None = None) -> Any: ...
    async def delete(self) -> None: ...

    @property
    def files(self) -> Any: ...

    @property
    def checkpoints(self) -> Any: ...

    @property
    def egress(self) -> Any: ...


class Sandbox:
    """One Runta runtime."""

    def __init__(self, runtime: RuntimeLike) -> None:
        self.runtime = runtime
        self.created_at = time.monotonic()

    @property
    def id(self) -> str:
        return self.runtime.id

    async def sh(self, command: str, timeout: float = 300.0, check: bool = False) -> ExecOutcome:
        start = time.monotonic()
        result = await self.runtime.exec(["bash", "-lc", command], timeout=timeout)
        wall = time.monotonic() - start
        out = ExecOutcome(
            exit_code=int(result.exit_code),
            stdout=_text(getattr(result, "stdout_text", None), getattr(result, "stdout", b"")),
            stderr=_text(getattr(result, "stderr_text", None), getattr(result, "stderr", b"")),
            wall_s=wall,
        )
        if check and out.exit_code != 0:
            tail = (out.stderr or out.stdout)[-400:]
            raise SandboxError(f"exec `{command[:60]}`", f"exit {out.exit_code}: {tail}")
        return out

    async def put_dir(self, local_dir: Path, remote_dir: str) -> None:
        """Upload a local folder as one tar file, then unpack it inside the runtime."""
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            tar.add(local_dir, arcname=".")
        remote_tar = f"/tmp/fb-upload-{uuid.uuid4().hex[:8]}.tar"  # noqa: S108 - path in the remote sandbox
        await self.runtime.files.write(remote_tar, buf.getvalue())
        await self.sh(
            f"mkdir -p {shlex.quote(remote_dir)} && tar -xf {remote_tar} -C {shlex.quote(remote_dir)}"
            f" && rm -f {remote_tar}",
            check=True,
        )


def _text(preferred: Any, raw: Any) -> str:
    if isinstance(preferred, str):
        return preferred
    if isinstance(raw, bytes):
        return raw.decode(errors="replace")
    return str(raw or "")


@dataclass
class Timed:
    value: Any
    seconds: float


class Gateway:
    """Creates sandboxes/checkpoints and tracks them for guaranteed cleanup."""

    def __init__(self, client: Any, vcpus: int, memory_mib: int, ready_timeout_s: float) -> None:
        self._client = client
        self._vcpus = vcpus
        self._memory_mib = memory_mib
        self._ready_timeout_s = ready_timeout_s
        self._live: dict[str, Sandbox] = {}
        self._checkpoints: set[str] = set()

    async def create(self, name: str, egress_policy: Any = None) -> Timed:
        start = time.monotonic()
        try:
            runtime = await self._client.runtimes.create(
                name,
                vcpus=self._vcpus,
                memory_mib=self._memory_mib,
                egress_policy=egress_policy,
                wait=True,
                timeout=self._ready_timeout_s,
                poll_interval=POLL_S,
            )
        except Exception as exc:  # noqa: BLE001 - surface any SDK error with its step name
            raise SandboxError("create runtime", repr(exc)) from exc
        sandbox = Sandbox(runtime)
        self._live[sandbox.id] = sandbox
        return Timed(sandbox, time.monotonic() - start)

    async def restore(self, checkpoint_id: str, name: str, egress_policy: Any = None) -> Timed:
        start = time.monotonic()
        try:
            runtime = await self._client.runtimes.create(
                name,
                checkpoint_id=checkpoint_id,
                egress_policy=egress_policy,
                wait=True,
                timeout=self._ready_timeout_s,
                poll_interval=POLL_S,
            )
        except Exception as exc:  # noqa: BLE001
            raise SandboxError("restore runtime", repr(exc)) from exc
        sandbox = Sandbox(runtime)
        self._live[sandbox.id] = sandbox
        return Timed(sandbox, time.monotonic() - start)

    async def checkpoint(self, sandbox: Sandbox, name: str) -> Timed:
        start = time.monotonic()
        try:
            info = await sandbox.runtime.checkpoints.create(
                name=name, wait=True, timeout=self._ready_timeout_s, poll_interval=POLL_S
            )
        except Exception as exc:  # noqa: BLE001
            raise SandboxError("create checkpoint", repr(exc)) from exc
        self._checkpoints.add(info.id)
        state = str(getattr(info, "state", "")).lower()
        if "ready" not in state:
            raise SandboxError("create checkpoint", f"ended in state {state!r}")
        return Timed(info.id, time.monotonic() - start)

    async def delete(self, sandbox: Sandbox) -> float:
        """Delete a runtime. Only forget it once the delete succeeded, so that a failed delete
        (e.g. a network blip) is retried by the final cleanup() instead of leaking a billing runtime."""
        start = time.monotonic()
        await sandbox.runtime.delete()
        self._live.pop(sandbox.id, None)
        return time.monotonic() - start

    async def discard(self, sandbox: Sandbox) -> None:
        """Best-effort delete after a failed setup. Shielded so Ctrl-C can't skip it; never raises."""
        try:
            await asyncio.shield(self.delete(sandbox))
        except BaseException as exc:  # noqa: BLE001 - final cleanup() retries anything left
            log.warning("discard %s failed: %r", sandbox.id, exc)

    async def delete_checkpoint(self, checkpoint_id: str) -> None:
        await self._client.checkpoints.delete(checkpoint_id)
        self._checkpoints.discard(checkpoint_id)

    async def cleanup(self, keep_checkpoints: bool = False) -> list[str]:
        """Delete everything this gateway created. Returns human-readable failures (if any)."""
        failures: list[str] = []
        results = await asyncio.gather(
            *(sb.runtime.delete() for sb in self._live.values()), return_exceptions=True
        )
        for sb, res in zip(list(self._live.values()), results, strict=True):
            if isinstance(res, BaseException):
                failures.append(f"runtime {sb.id}: {res!r}")
        self._live.clear()
        if not keep_checkpoints:
            for cid in list(self._checkpoints):
                try:
                    await self.delete_checkpoint(cid)
                except Exception as exc:  # noqa: BLE001
                    failures.append(f"checkpoint {cid}: {exc!r}")
        for failure in failures:
            log.warning("cleanup failed: %s", failure)
        return failures


async def purge(client: Any, prefix: str) -> tuple[list[str], list[str]]:
    """Delete every runtime and checkpoint whose name starts with `prefix` (orphan cleanup)."""
    deleted_runtimes: list[str] = []
    for runtime in await client.runtimes.list():
        if str(runtime.name).startswith(prefix):
            await runtime.delete()
            deleted_runtimes.append(f"{runtime.name} ({runtime.id})")
    deleted_checkpoints: list[str] = []
    for ck in await client.checkpoints.list():
        if str(getattr(ck, "display_name", "")).startswith(prefix):
            await client.checkpoints.delete(ck.id)
            deleted_checkpoints.append(f"{ck.display_name} ({ck.id})")
    return deleted_runtimes, deleted_checkpoints
