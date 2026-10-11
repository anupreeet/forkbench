"""Validate the egress *measurement method* before trusting it (host vs Docker container).

Earlier probe flaw: a bare TCP connect (bash /dev/tcp) "succeeds" against a TLS-intercepting egress
gateway even when the request is later blocked. So here every probe is an application-level request
whose response body must contain a known marker; timeouts are reported separately from blocks.
"""

import asyncio
import json
import time
from datetime import UTC, datetime

from runta import AsyncRunta, EgressMode, EgressPolicy

TAG = f"fb-egc-{int(time.time())}"
IMAGE = "ghcr.io/laude-institute/terminal-bench/regex-log:2.0"
HTTP_REQ = ("exec 3<>/dev/tcp/{h}/80 && printf 'GET / HTTP/1.1\\r\\nHost: {h}\\r\\nConnection: close\\r\\n\\r\\n' >&3"
            " && timeout 8 cat <&3")
PROBES = {
    # host, HTTPS, curl exit codes distinguish timeout (28) from TLS/gateway refusal (35, 56, ...)
    "host_https_pypi": "curl -s -o /dev/null -w '%{http_code}' --max-time 10 https://pypi.org/simple/; echo \" exit=$?\"",
    # host, plain HTTP, body marker
    "host_http_example": "curl -s --max-time 10 http://example.com/ | head -c 2000 | grep -c 'Example Domain'; true",
    # container, plain HTTP via bash only (no curl in the base image), body marker + first line
    "ctr_http_example": "docker exec task timeout 12 bash -c \"" + HTTP_REQ.format(h="example.com").replace('"', '\\"')
                        + "\" 2>&1 | head -c 3000 | { read -r first; rest=$(cat); echo \"first=[$first] marker=$(printf '%s' \"$rest\" | grep -c 'Example Domain')\"; }",
}


async def probe(rt):
    out = {}
    for k, cmd in PROBES.items():
        r = await rt.exec(["bash", "-lc", cmd], timeout=60)
        out[k] = r.stdout_text.strip()[-200:]
    return out


async def main():
    res = {"tag": TAG, "started_utc": datetime.now(UTC).isoformat(), "phases": []}
    async with AsyncRunta() as c:
        rt = await c.runtimes.create(TAG, vcpus=2, memory_mib=4096)
        try:
            for cmd in (f"docker pull -q {IMAGE}",
                        f"docker run -d --name task {IMAGE} sh -c 'while true; do sleep 3600; done'",
                        "docker exec task sh -c 'command -v curl wget python3 perl || true'"):
                r = await rt.exec(["bash", "-lc", cmd], timeout=600)
                res.setdefault("setup", []).append({"cmd": cmd, "exit": r.exit_code, "out": r.stdout_text.strip()[-200:]})
            for label, policy, seconds in (
                ("open (default)", None, 0),
                ("deny-all (allowlist [])", EgressPolicy(mode=EgressMode.ALLOWLIST, allowed_hosts=[], denied_hosts=[]), 60),
                ("open again", EgressPolicy(), 50),
            ):
                t0 = time.monotonic()
                if policy is not None:
                    await rt.egress.set_policy(policy)
                samples = []
                while True:
                    samples.append({"t": round(time.monotonic() - t0, 1), **(await probe(rt))})
                    if time.monotonic() - t0 >= seconds:
                        break
                    await asyncio.sleep(4)
                res["phases"].append({"label": label, "samples": samples})
                print(json.dumps({"label": label, "first": samples[0], "last": samples[-1]}), flush=True)
        finally:
            await rt.delete()
    with open("evidence/17-egress-container-method.json", "w") as fh:
        json.dump(res, fh, indent=2)


asyncio.run(main())
