"""Re-measure how long an egress policy change takes to be enforced (host and task container).

Uses forkbench's validated probes: application-level requests (host HTTPS, container HTTP with a
body marker), timeouts reported separately from blocks. For each change: time the set_policy()
call, read the policy back, then probe every ~2 s until both host and container match.
Run: .venv/bin/python scripts/probe_egress_timing.py evidence/<out>.json
"""

import asyncio
import json
import sys
import time
from datetime import UTC, datetime

from runta import AsyncRunta, EgressMode, EgressPolicy

from forkbench.envsetup import start_task_env
from forkbench.sandbox import Sandbox
from forkbench.workload import egress_probe, egress_until

OUT = sys.argv[1]
NAME = f"fb-verify-egress-{int(time.time())}"
IMAGE = "ghcr.io/laude-institute/terminal-bench/regex-log:2.0"
DENY = EgressPolicy(mode=EgressMode.ALLOWLIST, allowed_hosts=[], denied_hosts=[])
OPEN = EgressPolicy()
CHANGES = [("deny-all", DENY, False), ("open", OPEN, True)] * 3


async def main() -> None:
    out: dict = {"name": NAME, "started_utc": datetime.now(UTC).isoformat(), "changes": []}
    async with AsyncRunta() as c:
        rt = await c.runtimes.create(NAME, vcpus=2, memory_mib=4096, poll_interval=1.0)
        try:
            sb = Sandbox(rt)
            await start_task_env(sb, IMAGE, 600)
            out["baseline"] = await egress_probe(sb)
            print(json.dumps({"baseline": out["baseline"]}), flush=True)
            for label, policy, want_open in CHANGES:
                t = time.monotonic()
                await rt.egress.set_policy(policy)
                call_s = round(time.monotonic() - t, 2)
                readback = str(await rt.egress.get())[:160]
                res = await egress_until(sb, want_open, limit_s=150)
                row = {"to": label, "utc": datetime.now(UTC).strftime("%H:%M:%S"), "set_policy_call_s": call_s,
                       "readback": readback, **res}
                out["changes"].append(row)
                print(json.dumps(row), flush=True)
                await asyncio.sleep(5)
        finally:
            await rt.delete()
    out["finished_utc"] = datetime.now(UTC).isoformat()
    with open(OUT, "w") as fh:
        json.dump(out, fh, indent=2)


asyncio.run(main())
