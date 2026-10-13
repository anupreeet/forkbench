"""Two checks on one small runtime.

1. Background processes: which patterns survive the end of an exec, via the Python SDK and via the
   CLI, including the docs' own example (`runta exec … -- sh -lc 'nohup python3 -m http.server … &'`).
2. Gateway CA in containers: for several common images, is Runta's CA/env injected, and does an
   HTTPS request through the TLS-intercepting gateway verify?
Run: .venv/bin/python scripts/probe_ca_and_background.py evidence/<out>.json
"""

import asyncio
import json
import shlex
import subprocess
import sys
import time
from datetime import UTC, datetime

from runta import AsyncRunta

OUT = sys.argv[1]
NAME = f"fb-verify-cabg-{int(time.time())}"
SDK_PATTERNS = {
    "sdk: sleep 801 &": "sleep 801 &",
    "sdk: nohup sleep 802 >/dev/null 2>&1 &": "nohup sleep 802 >/dev/null 2>&1 &",
    "sdk: setsid -f sleep 803": "setsid -f sleep 803 >/dev/null 2>&1 < /dev/null",
}
CLI_PATTERNS = {
    "cli: sleep 804 &": "sleep 804 &",
    "cli (docs example): nohup python3 -m http.server 8080 &": "nohup python3 -m http.server 8080 >/dev/null 2>&1 &",
}
INSPECT = ("env | grep -iE 'CA_BUNDLE|SSL_CERT|REQUESTS_CA|NODE_EXTRA' | sort | tr '\\n' ' '; echo; "
           "ls /run/runta/ca-bundle.pem /usr/local/share/ca-certificates/runta-egress.crt 2>&1 | tr '\\n' ' '")
IMAGES = {
    "curlimages/curl:8.10.1": "curl -sS -o /dev/null -w '%{http_code}' --max-time 15 https://example.com; echo \" exit=$?\"",
    "python:3.12-slim": "python3 -c \"import urllib.request;print(urllib.request.urlopen('https://example.com',timeout=15).status)\" 2>&1 | tail -1",
    "alpine:3.20": "wget -q -O /dev/null -T 15 https://example.com && echo ok || echo \"failed exit=$?\"",
}


def cli_exec(name: str, cmd: str) -> str:
    p = subprocess.run(["runta", "exec", name, "--", "sh", "-lc", cmd], capture_output=True, text=True, timeout=120)
    return (p.stdout + p.stderr).strip()[-300:]


async def main() -> None:
    out: dict = {"name": NAME, "started_utc": datetime.now(UTC).isoformat()}
    async with AsyncRunta() as c:
        rt = await c.runtimes.create(NAME, vcpus=1, memory_mib=2048, poll_interval=1.0)
        try:
            for cmd in SDK_PATTERNS.values():
                await rt.exec(["bash", "-lc", cmd])
            for cmd in CLI_PATTERNS.values():
                await asyncio.to_thread(cli_exec, NAME, cmd)
            await asyncio.sleep(5)
            alive = (await rt.exec(["bash", "-lc", "pgrep -fa '[s]leep 80[1-4]|[h]ttp.server 8080' || true"])).stdout_text
            srv = (await rt.exec(["bash", "-lc", "curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:8080/ || true"])).stdout_text
            keys = {"sdk: sleep 801 &": "sleep 801", "sdk: nohup sleep 802 >/dev/null 2>&1 &": "sleep 802",
                    "sdk: setsid -f sleep 803": "sleep 803", "cli: sleep 804 &": "sleep 804",
                    "cli (docs example): nohup python3 -m http.server 8080 &": "http.server 8080"}
            out["background_5s_later"] = {label: ("alive" if key in alive else "gone") for label, key in keys.items()}
            out["background_5s_later"]["docs example server answers on :8080"] = srv.strip() or "no answer"
            out["pgrep"] = alive.strip()
            out["containers"] = {}
            for image, req in IMAGES.items():
                pull = await rt.exec(["bash", "-lc", f"docker pull -q {image} >/dev/null 2>&1; echo $?"], timeout=300)
                r = await rt.exec(["bash", "-lc", f"docker run --rm --entrypoint sh {image} -c {shlex.quote(INSPECT + '; echo; ' + req)}"],
                                  timeout=120)
                out["containers"][image] = {"pull_exit": pull.stdout_text.strip(),
                                            "output": (r.stdout_text + r.stderr_text).strip()[-500:]}
                print(json.dumps({image: out["containers"][image]}), flush=True)
        finally:
            await rt.delete()
    out["finished_utc"] = datetime.now(UTC).isoformat()
    print(json.dumps(out, indent=2))
    with open(OUT, "w") as fh:
        json.dump(out, fh, indent=2)


asyncio.run(main())
