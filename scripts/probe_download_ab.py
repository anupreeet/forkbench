"""GitHub release download: default egress path vs TLS passthrough, with replicated runtimes.

Two runtimes per arm (so one slow machine can't explain a difference), plus this machine as an
outside control. Every round, all five download the same 17.8 MB release asset at the same time
(max 120 s each). Run: .venv/bin/python scripts/probe_download_ab.py evidence/<out>.json
"""

import asyncio
import json
import statistics
import subprocess
import sys
import time
from datetime import UTC, datetime

from runta import AsyncRunta

OUT = sys.argv[1]
URL = "https://github.com/astral-sh/uv/releases/download/0.7.13/uv-x86_64-unknown-linux-gnu.tar.gz"
FMT = "%{http_code} %{size_download} %{time_total}"
CMD = f"curl -sS -L -o /dev/null --max-time 120 -w '{FMT}' '{URL}' 2>/dev/null; echo \" $?\""
ROUNDS, GAP_S = 10, 30
TS = int(time.time())
ARMS = {"gateway-1": False, "gateway-2": False, "passthrough-1": True, "passthrough-2": True}


def create(name: str, passthrough: bool) -> None:
    args = ["runta", "run", "--name", name, "--cpus", "1", "--memory", "1024", "--wait"]
    if passthrough:
        args += ["--tls-passthrough-host", "github.com", "--tls-passthrough-host", "*.githubusercontent.com"]
    subprocess.run(args, capture_output=True, text=True, timeout=600, check=True)


def parse(raw: str) -> dict:
    parts = raw.split()
    if len(parts) < 4:
        return {"raw": raw[-200:]}
    code, size, t, rc = parts[:4]
    return {"code": code, "mb": round(int(size) / 1e6, 1), "s": float(t), "exit": int(rc),
            "ok": code == "200" and int(size) > 17_000_000}


async def remote(rt) -> dict:
    try:
        r = await rt.exec(["bash", "-lc", CMD], timeout=180)
    except Exception as exc:  # noqa: BLE001 - record transport errors instead of losing the round
        return {"raw": f"EXEC_ERROR {type(exc).__name__}: {str(exc)[:120]}"}
    return parse(r.stdout_text.strip())


async def local() -> dict:
    p = await asyncio.create_subprocess_shell(CMD, stdout=asyncio.subprocess.PIPE)
    out, _ = await p.communicate()
    return parse(out.decode().strip())


async def main() -> None:
    names = {arm: f"fb-verify-dl-{arm}-{TS}" for arm in ARMS}
    res: dict = {"started_utc": datetime.now(UTC).isoformat(), "url": URL, "rounds": []}
    created = await asyncio.gather(*(asyncio.to_thread(create, names[a], p) for a, p in ARMS.items()),
                                   return_exceptions=True)
    async with AsyncRunta() as c:
        rts = {}
        try:
            for arm, err in zip(ARMS, created, strict=True):
                if isinstance(err, Exception):
                    raise RuntimeError(f"create {arm} failed: {err}")
                rts[arm] = await c.runtimes.get(names[arm])
            for arm, rt in rts.items():
                r = await rt.exec(["bash", "-lc", "curl -sv -o /dev/null --max-time 20 https://github.com 2>&1 | grep -m1 'issuer:'"],
                                  timeout=40)
                res.setdefault("issuer", {})[arm] = r.stdout_text.strip()
            for i in range(ROUNDS):
                t0 = time.monotonic()
                results = await asyncio.gather(*(remote(rt) for rt in rts.values()), local())
                row = {"round": i + 1, "utc": datetime.now(UTC).strftime("%H:%M:%S"),
                       **dict(zip([*rts, "local-control"], results, strict=True))}
                res["rounds"].append(row)
                print(json.dumps(row), flush=True)
                await asyncio.sleep(max(0, GAP_S - (time.monotonic() - t0)))
        finally:
            await asyncio.gather(*(rt.delete() for rt in rts.values()), return_exceptions=True)
    summary = {}
    for arm in [*ARMS, "local-control"]:
        rows = [r[arm] for r in res["rounds"] if arm in r]
        ok = [x["s"] for x in rows if x.get("ok")]
        summary[arm] = {"ok": f"{len(ok)}/{len(rows)}", "median_s_ok": round(statistics.median(ok), 2) if ok else None,
                        "max_s_ok": max(ok) if ok else None}
    res["summary"] = summary
    res["finished_utc"] = datetime.now(UTC).isoformat()
    print(json.dumps(summary), flush=True)
    with open(OUT, "w") as fh:
        json.dump(res, fh, indent=2)


asyncio.run(main())
