"""Attribute intermittent GitHub download stalls: watch three paths side by side over time.

  A    default runtime (traffic goes through Runta's TLS-intercepting egress gateway)
  B    runtime with --tls-passthrough-host for GitHub hosts (gateway does not intercept them)
  M    this Mac (outside Runta)

Every ~30 s, all three download the same 17.8 MB release asset concurrently (max 120 s each).
If A stalls while B and M don't, the gateway path is implicated; if A and B both stall, upstream.
"""

import asyncio
import json
import sys
import subprocess
import time
from datetime import UTC, datetime

from runta import AsyncRunta

URL = "https://github.com/astral-sh/uv/releases/download/0.7.13/uv-x86_64-unknown-linux-gnu.tar.gz"
FMT = "%{http_code} %{size_download} %{time_total}"
CMD = f"curl -sS -L -o /dev/null --max-time 120 -w '{FMT}' '{URL}' 2>/dev/null; echo \" $?\""
ROUNDS, GAP_S = 14, 30
TS = int(time.time())


def create(name: str, passthrough: bool) -> None:
    args = ["runta", "run", "--name", name, "--cpus", "1", "--memory", "1024", "--wait"]
    if passthrough:
        args += ["--tls-passthrough-host", "github.com", "--tls-passthrough-host", "*.githubusercontent.com"]
    subprocess.run(args, capture_output=True, text=True, timeout=600, check=True)


def parse(raw: str) -> dict:
    parts = raw.split()
    if len(parts) < 4:
        return {"raw": raw}
    code, size, t, rc = parts[:4]
    return {"code": code, "mb": round(int(size) / 1e6, 1), "s": float(t), "exit": int(rc),
            "ok": code == "200" and int(size) > 17_000_000}


async def remote(rt) -> dict:
    r = await rt.exec(["bash", "-lc", CMD], timeout=180)
    return parse(r.stdout_text.strip())


async def local() -> dict:
    p = await asyncio.create_subprocess_shell(CMD, stdout=asyncio.subprocess.PIPE)
    out, _ = await p.communicate()
    return parse(out.decode().strip())


async def main():
    a, b = f"fb-dlw-a-{TS}", f"fb-dlw-b-{TS}"
    res = {"started_utc": datetime.now(UTC).isoformat(), "url": URL, "rounds": []}
    await asyncio.gather(asyncio.to_thread(create, a, False), asyncio.to_thread(create, b, True))
    async with AsyncRunta() as c:
        ra, rb = await c.runtimes.get(a), await c.runtimes.get(b)
        try:
            for i in range(ROUNDS):
                t0 = time.monotonic()
                A, B, M = await asyncio.gather(remote(ra), remote(rb), local())
                row = {"round": i + 1, "utc": datetime.now(UTC).strftime("%H:%M:%S"), "A_gateway": A,
                       "B_passthrough": B, "M_mac": M}
                res["rounds"].append(row)
                print(json.dumps(row), flush=True)
                await asyncio.sleep(max(0, GAP_S - (time.monotonic() - t0)))
        finally:
            await asyncio.gather(ra.delete(), rb.delete(), return_exceptions=True)
    with open(sys.argv[1] if len(sys.argv) > 1 else "evidence/19-download-watch.json", "w") as fh:
        json.dump(res, fh, indent=2)


asyncio.run(main())
