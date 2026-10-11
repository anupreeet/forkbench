"""What runs inside each acquired environment: the task workload plus correctness checks."""

from __future__ import annotations

import asyncio
import shlex
import statistics
import time
from typing import Any

from forkbench.envsetup import copy_into_container, ctr
from forkbench.sandbox import Sandbox, SandboxError
from forkbench.taskspec import TaskSpec

MARKER = "fb-leak-marker"


async def run_workload(sb: Sandbox, task: TaskSpec, kind: str) -> dict[str, Any]:
    """kind='oracle': upload + run solution, then tests (expect reward 1).
    kind='nop': tests only (expect reward 0). Mirrors Harbor's oracle/nop agents + verifier paths."""
    timings: dict[str, float] = {}
    solve_tail = ""
    if kind == "oracle":
        t = time.monotonic()
        await copy_into_container(sb, task.solution_dir, "/solution")
        solve = await ctr(
            sb,
            "bash /solution/solve.sh > /logs/agent/oracle.txt 2>&1",
            timeout=task.agent_timeout_s,
        )
        timings["solve_s"] = time.monotonic() - t
        if solve.exit_code != 0:
            # Harbor does not fail the trial on a non-zero solve.sh; the verifier decides. Keep the end
            # of the solution's output so a failure can be attributed (e.g. its own downloads failing).
            timings["solve_exit"] = float(solve.exit_code)
            tail = await ctr(sb, "tail -c 800 /logs/agent/oracle.txt 2>/dev/null")
            solve_tail = tail.stdout[-800:]
    elif kind != "nop":
        raise ValueError(f"unknown workload {kind!r}")

    t = time.monotonic()
    await copy_into_container(sb, task.tests_dir, "/tests")
    await ctr(
        sb,
        "mkdir -p /logs/verifier && bash /tests/test.sh > /logs/verifier/test-output.txt 2>&1",
        timeout=task.verifier_timeout_s,
    )
    timings["verify_s"] = time.monotonic() - t
    reward = await read_reward(sb)
    # Keep the end of the verifier output so any unexpected reward can be explained from evidence
    # (e.g. the verifier's own network downloads failing, unrelated to the reset strategy).
    tail = await ctr(sb, "tail -c 1200 /logs/verifier/test-output.txt 2>/dev/null")
    return {
        "reward": reward,
        "timings": timings,
        "verifier_tail": tail.stdout[-1200:],
        "solve_tail": solve_tail,
    }


async def read_reward(sb: Sandbox) -> float | None:
    out = await ctr(
        sb,
        "cat /logs/verifier/reward.txt 2>/dev/null || cat /logs/verifier/reward.json 2>/dev/null",
    )
    text = out.stdout.strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        import json

        data = json.loads(text)
        values = list(data.values()) if isinstance(data, dict) else [data]
        return float(values[0]) if values else None


async def leak_check(sb: Sandbox, trial_name: str) -> dict[str, Any]:
    """A fresh environment must not contain markers written by any earlier trial."""
    host = await sb.sh(f"cat /root/{MARKER} 2>/dev/null || echo ABSENT")
    inside = await ctr(sb, f"cat /tmp/{MARKER} 2>/dev/null || echo ABSENT")
    await sb.sh(f"echo {trial_name} > /root/{MARKER}")
    await ctr(sb, f"echo {trial_name} > /tmp/{MARKER}")
    host_seen, inside_seen = host.stdout.strip(), inside.stdout.strip()
    return {
        "host_seen": host_seen,
        "container_seen": inside_seen,
        "clean": host_seen == "ABSENT" and inside_seen == "ABSENT",
    }


async def clock_check(sb: Sandbox) -> dict[str, Any]:
    """Skew between the runtime clock and ours, corrected for the round trip."""
    before = time.time()
    out = await sb.sh("date +%s.%N")
    after = time.time()
    try:
        remote = float(out.stdout.strip())
    except ValueError as exc:
        raise SandboxError("clock check", out.stdout[-100:]) from exc
    return {"skew_s": round(remote - (before + after) / 2, 3), "rtt_s": round(after - before, 3)}


async def identity(sb: Sandbox) -> dict[str, str]:
    """Recorded, not judged: values that may be duplicated across copies of one snapshot."""
    out = await sb.sh(
        "cat /proc/sys/kernel/random/boot_id; cat /etc/machine-id 2>/dev/null || echo none; "
        "hostname; cat /proc/sys/kernel/random/uuid"
    )
    parts = (out.stdout.strip().splitlines() + ["", "", "", ""])[:4]
    return dict(zip(("boot_id", "machine_id", "hostname", "fresh_uuid"), parts, strict=True))


async def exec_latency(sb: Sandbox, samples: int = 5) -> dict[str, float]:
    """Wall-clock round trip of a no-op command (`true`), measured from this machine."""
    walls = [(await sb.sh("true")).wall_s for _ in range(samples)]
    return {"wall_median_s": round(statistics.median(walls), 3), "n": float(samples)}


_HTTP_GET = (
    "exec 3<>/dev/tcp/example.com/80 && "
    "printf 'GET / HTTP/1.1\\r\\nHost: example.com\\r\\nConnection: close\\r\\n\\r\\n' >&3 && "
    "timeout 8 cat <&3"
)


def _classify(ok: bool, timed_out: bool, detail: str) -> str:
    if ok:
        return "OPEN"
    return "TIMEOUT" if timed_out else f"BLOCKED({detail[:40]})"


async def egress_probe(sb: Sandbox) -> dict[str, str]:
    """Application-level reachability from the runtime host and from the task container.

    Runta's egress gateway intercepts TLS, so a bare TCP connect can succeed even when the request
    is then refused. Each probe therefore checks real response content, and timeouts are reported
    separately from refusals (a slow site must not look like an enforced block).
    """
    https = await sb.sh(
        "curl -s -o /dev/null -w '%{http_code}' --max-time 10 https://pypi.org/simple/; echo \" $?\""
    )
    code, _, rc = https.stdout.strip().partition(" ")
    host = _classify(code[:1] in "23" and code != "000", rc == "28", f"{code}/curl {rc}")
    inner = await ctr(sb, f"timeout 12 bash -c {shlex.quote(_HTTP_GET)}", timeout=30)
    body = inner.stdout
    first = (body.splitlines() or [""])[0].strip()
    container = _classify(
        "Example Domain" in body, inner.exit_code == 124, first or f"exit {inner.exit_code}"
    )
    return {"host": host, "container": container}


async def egress_until(sb: Sandbox, want_open: bool, limit_s: float = 120.0) -> dict[str, Any]:
    """Probe every ~2 s until both host and container match `want_open` (or the limit passes).

    A policy *change* may not apply instantly, so a single probe could misreport enforcement.
    """
    t0 = time.monotonic()
    samples = 0
    while True:
        probe = await egress_probe(sb)
        samples += 1
        decided = all(v != "TIMEOUT" for v in probe.values())
        matches = decided and all((v == "OPEN") == want_open for v in probe.values())
        elapsed = round(time.monotonic() - t0, 1)
        if matches or elapsed >= limit_s:
            return {
                "want": "OPEN" if want_open else "BLOCKED",
                "last": probe,
                "matched": matches,
                "after_s": elapsed,
                "samples": samples,
            }
        await asyncio.sleep(2)
