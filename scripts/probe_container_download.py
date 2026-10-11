"""Do large HTTPS downloads behave differently inside Docker containers than on the runtime host?

Every failed verifier download so far ran inside a task container, while host-level downloads
succeeded. Same runtime, same minute, three paths:
  host        curl on the runtime itself
  bridge      curl in a container on Docker's default bridge network
  hostnet     curl in a container with --network host (bypasses the bridge)
Also records interface MTUs and Docker's daemon config (hypothesis: bridge MTU > VM MTU).
"""

import asyncio
import json
import time
from datetime import UTC, datetime

from runta import AsyncRunta

TAG = f"fb-cdl-{int(time.time())}"
CURL_IMG = "curlimages/curl:8.10.1"
FMT = "code=%{http_code} size=%{size_download} t=%{time_total}s"
TARGETS = {
    "uv_github": "https://github.com/astral-sh/uv/releases/download/0.7.13/uv-x86_64-unknown-linux-gnu.tar.gz",
    "cf_20MB": "https://speed.cloudflare.com/__down?bytes=20000000",
}


async def sh(rt, cmd, timeout=300):
    t = time.monotonic()
    r = await rt.exec(["bash", "-lc", cmd], timeout=timeout)
    return {"exit": r.exit_code, "s": round(time.monotonic() - t, 1),
            "out": (r.stdout_text.strip() + " " + r.stderr_text.strip()).strip()[-300:]}


def curl(path: str, url: str) -> str:
    base = f"curl -sS -L -o /dev/null --max-time 150 -w '{FMT}' '{url}'; echo \" exit=$?\""
    if path == "host":
        return base
    net = "--network host" if path == "hostnet" else ""
    return (f"docker run --rm {net} --entrypoint sh {CURL_IMG} -c "
            f"\"curl -sS -L -o /dev/null --max-time 150 -w '{FMT}' '{url}'; echo \\\" exit=\\$?\\\"\"")


async def main():
    out = {"tag": TAG, "started_utc": datetime.now(UTC).isoformat(), "rounds": []}
    async with AsyncRunta() as c:
        rt = await c.runtimes.create(TAG, vcpus=2, memory_mib=4096)
        try:
            out["mtu"] = await sh(rt, "ip -o link | awk '{print $2, $4, $5}'")
            out["daemon_json"] = await sh(rt, "cat /etc/docker/daemon.json 2>/dev/null || echo none")
            out["pull"] = await sh(rt, f"docker pull -q {CURL_IMG}", timeout=600)
            out["mtu_after_docker"] = await sh(rt, "ip -o link | awk '{print $2, $4, $5}'; "
                                                   "docker network inspect bridge -f '{{json .Options}}'")
            for i in range(1, 5):
                for name, url in TARGETS.items():
                    if name == "cf_20MB" and i > 2:
                        continue
                    row = {"round": i, "target": name}
                    for path in ("host", "bridge", "hostnet"):
                        row[path] = (await sh(rt, curl(path, url), timeout=200))["out"]
                    out["rounds"].append(row)
                    print(json.dumps(row), flush=True)
        finally:
            await rt.delete()
    with open("evidence/18-container-download.json", "w") as fh:
        json.dump(out, fh, indent=2)


asyncio.run(main())
