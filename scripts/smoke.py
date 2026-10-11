"""Step 2 smoke test: create one runtime, inspect it, delete it. Times every step."""
import asyncio, json, time
from datetime import datetime, timezone
from runta import AsyncRunta

CMDS = {
    "os": "cat /etc/os-release | head -3; uname -m",
    "cpu_mem": "nproc; free -m | head -2",
    "disk": "df -h / | tail -1",
    "docker": "command -v docker && docker version --format '{{.Server.Version}}' 2>&1 || echo 'docker: not available'",
    "clock": "date -u +%s",
    "whoami": "id; echo HOME=$HOME",
}

async def main() -> None:
    name = f"fb-smoke-{int(time.time())}"
    out: dict = {"name": name, "started_utc": datetime.now(timezone.utc).isoformat()}
    async with AsyncRunta() as client:
        t0 = time.monotonic()
        rt = await client.runtimes.create(name, vcpus=2, memory_mib=4096)  # wait=True by default
        out["create_s"] = round(time.monotonic() - t0, 2)
        out["runtime_id"] = rt.id
        try:
            info = await rt.refresh_info()
            out["info"] = {k: str(v) for k, v in vars(info).items()} if hasattr(info, "__dict__") else str(info)
            for key, cmd in CMDS.items():
                t = time.monotonic()
                res = await rt.exec(["bash", "-lc", cmd], timeout=60)
                out[f"exec_{key}"] = {"exit": res.exit_code, "s": round(time.monotonic() - t, 2),
                                      "stdout": res.stdout_text.strip(), "stderr": res.stderr_text.strip()[:500]}
            out["local_clock"] = int(time.time())
        finally:
            t = time.monotonic()
            await rt.delete()
            out["delete_s"] = round(time.monotonic() - t, 2)
    print(json.dumps(out, indent=2, default=str))

asyncio.run(main())
