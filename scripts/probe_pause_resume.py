"""Explicit pause -> resume latency (n=3), the operation third-party sandbox benchmarks call "resume".

Also checks whether a process and a file survive the pause, so "resume" means the same machine state.
Readiness = first successful exec after resume() returns (the same definition LogRocket used:
"ready" is when a command actually executes).
"""

import asyncio
import json
import time
from datetime import UTC, datetime

from runta import AsyncRunta

NAME = f"fb-pause-{int(time.time())}"


async def first_exec(rt) -> float:
    t = time.monotonic()
    while True:
        try:
            r = await rt.exec(["true"], timeout=30)
            if r.exit_code == 0:
                return time.monotonic() - t
        except Exception:  # noqa: BLE001 - keep trying until the runtime accepts commands
            await asyncio.sleep(1)


async def main():
    out = {"name": NAME, "started_utc": datetime.now(UTC).isoformat(), "cycles": []}
    async with AsyncRunta() as c:
        t = time.monotonic()
        rt = await c.runtimes.create(NAME, vcpus=1, memory_mib=1024, poll_interval=1.0)
        out["create_s"] = round(time.monotonic() - t, 1)
        try:
            await rt.exec(["bash", "-lc", "echo kept > /root/state.txt; setsid -f sleep 3000 >/dev/null 2>&1"])
            for i in range(3):
                t0 = time.monotonic()
                await rt.pause()
                pause_call = time.monotonic() - t0
                st = None
                for _ in range(120):
                    st = str(await rt.status())
                    if "PAUSED" in st.upper():
                        break
                    await asyncio.sleep(1)
                paused_after = time.monotonic() - t0
                await asyncio.sleep(5)
                t1 = time.monotonic()
                try:
                    await rt.resume(wait=True, timeout=600, poll_interval=1.0)
                except Exception as exc:  # record (e.g. 429 "no available compute capacity"), retry once
                    out.setdefault("resume_errors", []).append({"cycle": i + 1, "after_s": round(time.monotonic() - t1, 1),
                                                                 "error": f"{type(exc).__name__}: {exc}"[:300]})
                    print(json.dumps(out["resume_errors"][-1]), flush=True)
                    await asyncio.sleep(30)
                    await rt.resume(wait=True, timeout=600, poll_interval=1.0)
                resume_call = time.monotonic() - t1
                ready = await first_exec(rt)
                chk = await rt.exec(["bash", "-lc", "cat /root/state.txt; pgrep -f 'sleep 3000' >/dev/null && echo proc-alive || echo proc-gone"])
                cycle = {"cycle": i + 1, "pause_call_s": round(pause_call, 1), "status_paused_after_s": round(paused_after, 1),
                         "status_seen": st, "resume_call_s": round(resume_call, 1), "first_exec_after_resume_s": round(ready, 1),
                         "resume_to_ready_s": round(resume_call + ready, 1), "state": chk.stdout_text.strip().replace("\n", " | ")}
                out["cycles"].append(cycle)
                print(json.dumps(cycle), flush=True)
        finally:
            await rt.delete()
    with open("evidence/23-pause-resume.json", "w") as fh:
        json.dump(out, fh, indent=2)


asyncio.run(main())
