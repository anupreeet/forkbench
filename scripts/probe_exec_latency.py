"""Split exec round-trip time into connection setup vs command.

runta-sdk 0.2.1 opens a new WebSocket (wss://…/v2/runtimes/<id>/exec/stream) for every exec call.
This probe times the WebSocket open (TCP + TLS + upgrade) separately from the rest of the call
(start frame → exit frame), for `true` x N, and compares with plain HTTPS requests from the same
machine. Run: .venv/bin/python scripts/probe_exec_latency.py evidence/<out>.json
"""

import asyncio
import json
import statistics
import subprocess
import sys
import time
from datetime import UTC, datetime

import runta._internal.streaming_transfer as st
from runta import AsyncRunta

OUT = sys.argv[1]
N = 20
NAME = f"fb-verify-exec-{int(time.time())}"
_opened: list[float] = []
_orig_connect = st.async_connect


class _TimedConnect:
    """Wraps websockets' connect() so `async with` records how long the socket took to open."""

    def __init__(self, *a, **kw):
        self._inner = _orig_connect(*a, **kw)

    async def __aenter__(self):
        t = time.monotonic()
        ws = await self._inner.__aenter__()
        _opened.append(time.monotonic() - t)
        return ws

    async def __aexit__(self, *exc):
        return await self._inner.__aexit__(*exc)


st.async_connect = _TimedConnect


def summary(xs: list[float]) -> dict:
    return {"n": len(xs), "median": round(statistics.median(xs), 3), "min": round(min(xs), 3), "max": round(max(xs), 3)}


def curl_times(url: str, n: int = 10) -> dict:
    fmt = "%{time_connect} %{time_appconnect} %{time_total}"
    rows = []
    for _ in range(n):
        p = subprocess.run(["curl", "-s", "-o", "/dev/null", "-w", fmt, "--max-time", "10", url],
                           capture_output=True, text=True, timeout=15)
        rows.append([float(x) for x in p.stdout.split()])
    return {"tcp_connect": summary([r[0] for r in rows]), "tls_done": summary([r[1] for r in rows]),
            "total": summary([r[2] for r in rows])}


async def main() -> None:
    out: dict = {"name": NAME, "started_utc": datetime.now(UTC).isoformat()}
    async with AsyncRunta() as c:
        endpoint = c._http.endpoint
        out["healthz_fresh_https"] = curl_times(f"{endpoint.rstrip('/')}/healthz")
        rt = await c.runtimes.create(NAME, vcpus=1, memory_mib=1024, poll_interval=1.0)
        try:
            await rt.exec(["true"])  # warm-up, not counted
            _opened.clear()
            total, connect = [], []
            for _ in range(N):
                t = time.monotonic()
                r = await rt.exec(["true"])
                total.append(time.monotonic() - t)
                connect.append(_opened[-1])
                assert r.exit_code == 0
            after = [a - b for a, b in zip(total, connect, strict=True)]
            out["exec_true"] = {"total": summary(total), "websocket_open": summary(connect),
                                "after_open_until_exit": summary(after)}
            r = await rt.exec(["bash", "-lc", "t0=$(date +%s%N); true; echo $(( ($(date +%s%N)-t0)/1000 ))us"])
            out["in_runtime_true"] = r.stdout_text.strip()
        finally:
            await rt.delete()
    out["finished_utc"] = datetime.now(UTC).isoformat()
    print(json.dumps(out, indent=2))
    with open(OUT, "w") as fh:
        json.dump(out, fh, indent=2)


asyncio.run(main())
