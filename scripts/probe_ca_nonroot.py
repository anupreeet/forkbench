"""Why does HTTPS fail in curlimages/curl when Runta's CA is injected? Hypothesis: the image runs as a
non-root user that can't read the injected bundle. Same image as root vs default user, and a root
image (python:3.12-slim) as root vs --user 1000, on two separate runtimes (two reproductions).
Run: .venv/bin/python scripts/probe_ca_nonroot.py evidence/<out>.json
"""

import asyncio
import json
import shlex
import sys
import time
from datetime import UTC, datetime

from runta import AsyncRunta

OUT = sys.argv[1]
TS = int(time.time())
INFO = "id -u; ls -ln /run/runta/ca-bundle.pem /usr/local/share/ca-certificates/runta-egress.crt 2>&1; head -c 30 /run/runta/ca-bundle.pem >/dev/null 2>&1 && echo bundle-readable || echo bundle-NOT-readable"
CURL = "curl -sS -o /dev/null -w '%{http_code}' --max-time 15 https://example.com; echo \" exit=$?\""
PY = "python3 -c \"import urllib.request;print(urllib.request.urlopen('https://example.com',timeout=15).status)\" 2>&1 | tail -1"
CASES = [
    ("curlimages/curl:8.10.1", "", CURL),
    ("curlimages/curl:8.10.1", "--user root", CURL),
    ("python:3.12-slim", "", PY),
    ("python:3.12-slim", "--user 1000", PY),
]


async def one(c: AsyncRunta, name: str) -> dict:
    rt = await c.runtimes.create(name, vcpus=1, memory_mib=2048, poll_interval=1.0)
    res: dict = {"runtime": name, "host_bundle": None, "cases": []}
    try:
        h = await rt.exec(["bash", "-lc", "ls -ln /run/runta/ca-bundle.pem /usr/local/share/ca-certificates/runta-egress.crt 2>&1"])
        res["host_bundle"] = h.stdout_text.strip()
        for image in {i for i, _, _ in CASES}:
            await rt.exec(["bash", "-lc", f"docker pull -q {image} >/dev/null 2>&1"], timeout=300)
        for image, user, req in CASES:
            r = await rt.exec(["bash", "-lc", f"docker run --rm {user} --entrypoint sh {image} -c {shlex.quote(INFO + '; ' + req)}"],
                              timeout=120)
            res["cases"].append({"image": image, "user_flag": user or "(image default)",
                                 "output": (r.stdout_text + r.stderr_text).strip().replace("\n", " | ")[-400:]})
    finally:
        await rt.delete()
    return res


async def main() -> None:
    out: dict = {"started_utc": datetime.now(UTC).isoformat()}
    async with AsyncRunta() as c:
        out["runs"] = await asyncio.gather(one(c, f"fb-verify-ca1-{TS}"), one(c, f"fb-verify-ca2-{TS}"))
    out["finished_utc"] = datetime.now(UTC).isoformat()
    print(json.dumps(out, indent=2))
    with open(OUT, "w") as fh:
        json.dump(out, fh, indent=2)


asyncio.run(main())
