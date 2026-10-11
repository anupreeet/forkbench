"""Runtime Image strategy check: create a runtime FROM a custom image built from a task image.

The Python SDK has no image parameter (finding L-14), so creation goes through the CLI
(`runta run --image <id>`); everything after that uses the SDK. Times creation, inspects the
environment, and runs the regex-log oracle + verifier directly in the runtime (no Docker).
"""

import asyncio
import io
import json
import os
import subprocess
import tarfile
import time
from datetime import UTC, datetime
from pathlib import Path

from runta import AsyncRunta

IMAGE_ID = "custom-01a128cd-b904-7703-b105-cc1f6bf876a6"  # built from FROM ghcr.io/.../regex-log:2.0
TASK = Path("tasks/tb2-sample/sample/regex-log")
NAME = f"fb-img-{int(time.time())}"


def tar_bytes(folder: Path) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        tar.add(folder, arcname=".")
    return buf.getvalue()


async def sh(rt, cmd, timeout=900):
    t = time.monotonic()
    r = await rt.exec(["bash", "-lc", cmd], timeout=timeout)
    return {"cmd": cmd[:80], "exit": r.exit_code, "s": round(time.monotonic() - t, 1),
            "out": r.stdout_text.strip()[-600:], "err": r.stderr_text.strip()[-300:]}


async def main():
    out = {"name": NAME, "image": IMAGE_ID, "started_utc": datetime.now(UTC).isoformat()}
    t = time.monotonic()
    p = subprocess.run(["runta", "run", "--name", NAME, "--image", IMAGE_ID, "--cpus", "2",
                        "--memory", "4096", "--wait"], capture_output=True, text=True, timeout=600)
    out["cli_create_s"] = round(time.monotonic() - t, 1)
    out["cli_create_exit"] = p.returncode
    out["cli_create_tail"] = (p.stdout + p.stderr)[-400:]
    async with AsyncRunta() as client:
        rt = await client.runtimes.get(NAME)
        try:
            out["env"] = await sh(rt, "head -2 /etc/os-release; echo PWD=$(pwd); ls -la /app | head; "
                                      "command -v python3 docker curl; id -un; ps -p 1 -o comm=")
            await rt.files.write("/tmp/sol.tar", tar_bytes(TASK / "solution"))
            await rt.files.write("/tmp/tests.tar", tar_bytes(TASK / "tests"))
            out["unpack"] = await sh(rt, "mkdir -p /solution /tests /logs/verifier /logs/agent && "
                                         "tar -xf /tmp/sol.tar -C /solution && tar -xf /tmp/tests.tar -C /tests")
            if os.environ.get("FB_TIMING_ONLY"):
                return
            out["solve"] = await sh(rt, "cd /app && bash /solution/solve.sh > /logs/agent/oracle.txt 2>&1; echo rc=$?")
            out["verify"] = await sh(rt, "cd /app && bash /tests/test.sh > /logs/verifier/test-output.txt 2>&1; "
                                         "echo reward=$(cat /logs/verifier/reward.txt); tail -c 300 /logs/verifier/test-output.txt")
        finally:
            await rt.delete()
            print(json.dumps(out, indent=2))


asyncio.run(main())
