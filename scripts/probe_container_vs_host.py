"""Same GitHub download, alternating between the runtime host and a task container, same runtime.

The container is prepared exactly like the Terminal-Bench verifier does it (apt-get install curl in
the regex-log task image), so its TLS trust path matches what verifiers see. Also records which
certificate issuer each side sees and where the container's CA bundle came from.
"""

import asyncio
import json
import shlex
import sys
import time
from datetime import UTC, datetime

from runta import AsyncRunta

NAME = f"fb-cvh-{int(time.time())}"
IMAGE = "ghcr.io/laude-institute/terminal-bench/regex-log:2.0"
URL = "https://github.com/astral-sh/uv/releases/download/0.7.13/uv-x86_64-unknown-linux-gnu.tar.gz"
CURL = f"curl -sS -L -o /dev/null --max-time 120 -w '%{{http_code}} %{{size_download}} %{{time_total}}' '{URL}'; echo \" $?\""
ROUNDS = 4


async def sh(rt, cmd, timeout=300):
    try:
        r = await rt.exec(["bash", "-lc", cmd], timeout=timeout)
    except Exception as exc:  # record transport errors (e.g. websocket closed) instead of crashing
        return f"EXEC_ERROR {type(exc).__name__}: {str(exc)[:160]}"
    return (r.stdout_text.strip() + " " + r.stderr_text.strip()).strip()


def parse(raw: str) -> dict:
    parts = raw.split()
    try:
        code, size, t, rc = parts[-4:]
        return {"code": code, "mb": round(int(size) / 1e6, 1), "s": float(t), "exit": int(rc),
                "ok": code == "200" and int(size) > 17_000_000}
    except (ValueError, IndexError):
        return {"raw": raw[-200:]}


async def main():
    out = {"name": NAME, "started_utc": datetime.now(UTC).isoformat(), "rounds": []}
    async with AsyncRunta() as c:
        rt = await c.runtimes.create(NAME, vcpus=2, memory_mib=4096, poll_interval=1.0)
        try:
            await sh(rt, f"docker pull -q {IMAGE} && docker run -d --name task {IMAGE} sh -c 'while true; do sleep 3600; done'", 600)
            out["container_setup"] = await sh(rt, "docker exec task bash -c 'apt-get update -qq >/dev/null && apt-get install -y -qq curl >/dev/null && curl --version | head -1'", 600)
            out["issuer_host"] = await sh(rt, "curl -sv -o /dev/null https://github.com 2>&1 | grep -m1 'issuer:'")
            out["issuer_container"] = await sh(rt, "docker exec task bash -c \"curl -sv -o /dev/null https://github.com 2>&1 | grep -m1 'issuer:'\"")
            out["container_ca_hint"] = await sh(rt, "docker exec task bash -c 'ls /usr/local/share/ca-certificates 2>&1; grep -c BEGIN /etc/ssl/certs/ca-certificates.crt; env | grep -i -E \"ca_bundle|ssl_cert|requests_ca\" || true'")
            out["docker_mounts"] = await sh(rt, "docker inspect task --format '{{json .Mounts}}'")
            for i in range(ROUNDS):
                host = parse(await sh(rt, CURL, 200))
                ctr = parse(await sh(rt, f"docker exec task bash -c {shlex.quote(CURL)}", 200))
                row = {"round": i + 1, "utc": datetime.now(UTC).strftime("%H:%M:%S"), "host": host, "container": ctr}
                out["rounds"].append(row)
                print(json.dumps(row), flush=True)
            print(json.dumps({k: out.get(k) for k in ("container_setup", "issuer_host", "issuer_container", "container_ca_hint")}), flush=True)
        finally:
            await rt.delete()
    with open(sys.argv[1] if len(sys.argv) > 1 else "evidence/25-container-vs-host.json", "w") as fh:
        json.dump(out, fh, indent=2)


asyncio.run(main())
