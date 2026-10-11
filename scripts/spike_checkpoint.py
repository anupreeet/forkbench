"""Step 3 spike: what does a Runta checkpoint actually preserve?

Source runtime: marker file + counting background process + running Docker task container.
Checkpoint it, delete the source, restore two copies, and inspect each copy.
Every step is timed. Every created resource is deleted in `finally`.
"""
import asyncio
import json
import sys
import time
from datetime import datetime, timezone

from runta import AsyncRunta

IMAGE = "ghcr.io/laude-institute/terminal-bench/regex-log:2.0"
TAG = f"fb-spike-{int(time.time())}"
LOG: dict = {"tag": TAG, "image": IMAGE, "started_utc": datetime.now(timezone.utc).isoformat(), "steps": []}


def now() -> float:
    return time.monotonic()


async def sh(rt, cmd: str, timeout: float = 300) -> dict:
    t = now()
    try:
        r = await rt.exec(["bash", "-lc", cmd], timeout=timeout)
        return {"cmd": cmd, "exit": r.exit_code, "s": round(now() - t, 2),
                "out": r.stdout_text.strip()[-1500:], "err": r.stderr_text.strip()[-800:]}
    except Exception as e:  # record, never abort the spike
        return {"cmd": cmd, "error": f"{type(e).__name__}: {e}", "s": round(now() - t, 2)}


def step(step_name: str, **data) -> None:
    entry = {"step": step_name, "utc": datetime.now(timezone.utc).isoformat(), **data}
    LOG["steps"].append(entry)
    print(json.dumps(entry, default=str), file=sys.stderr, flush=True)


INSPECT = {
    "marker": "cat /root/marker.txt 2>&1",
    "a_only": "cat /root/a-only.txt 2>&1 || echo ABSENT",
    "counter_pid_alive": "kill -0 $(cat /root/counter.pid) 2>&1 && echo ALIVE || echo DEAD",
    "counter_t0": "cat /root/counter",
    "counter_t3": "sleep 3; cat /root/counter",
    "docker_info": "docker info --format '{{.ServerVersion}} containers={{.Containers}} running={{.ContainersRunning}}' 2>&1",
    "docker_images": f"docker images --format '{{{{.Repository}}}}:{{{{.Tag}}}} {{{{.Size}}}}' 2>&1",
    "docker_ps": "docker ps -a --format '{{.Names}} {{.Status}}' 2>&1",
    "container_exec": "docker exec task cat /tmp/inside.txt 2>&1",
    "clock": "date -u +%s",
    "hostname": "hostname",
    "machine_id": "cat /etc/machine-id 2>&1",
    "net_host": "curl -s -o /dev/null -w '%{http_code}' --max-time 10 https://example.com 2>&1",
    "uptime": "cat /proc/uptime",
}


async def inspect(rt, label: str) -> dict:
    res = {}
    for k, cmd in INSPECT.items():
        res[k] = await sh(rt, cmd, timeout=60)
    res["local_clock"] = int(time.time())
    # If the container is not running, try to bring it back and time it.
    if "Up" not in res["docker_ps"].get("out", ""):
        res["docker_start"] = await sh(rt, "docker start task 2>&1", timeout=120)
        res["container_exec_after_start"] = await sh(rt, "docker exec task cat /tmp/inside.txt 2>&1", timeout=60)
    step(f"inspect_{label}", result=res)
    return res


async def main() -> None:
    created: list = []
    ckpt_id = None
    async with AsyncRunta() as client:
        try:
            t = now()
            src = await client.runtimes.create(f"{TAG}-src", vcpus=2, memory_mib=4096)
            created.append(src)
            step("create_source", s=round(now() - t, 2), runtime_id=src.id)

            setup = [
                "echo hello-from-source > /root/marker.txt",
                "nohup bash -c 'i=0; while true; do i=$((i+1)); echo $i > /root/counter; sleep 1; done' >/dev/null 2>&1 & echo $! > /root/counter.pid",
                f"docker pull -q {IMAGE}",
                f"docker run -d --name task {IMAGE} sh -c 'while true; do sleep 3600; done'",
                "docker exec task sh -c 'echo inside-container > /tmp/inside.txt'",
                "sleep 5",
            ]
            for cmd in setup:
                step("setup", result=await sh(src, cmd, timeout=600))
            await inspect(src, "source_before_checkpoint")

            t = now()
            ck = await src.checkpoints.create(name=f"{TAG}-ckpt", wait=True, timeout=900)
            ckpt_id = ck.id
            step("checkpoint_create", s=round(now() - t, 2), info=str(ck))

            # Does checkpointing disturb the source?
            step("source_status_after_checkpoint", status=str(await src.status()))
            await inspect(src, "source_after_checkpoint")

            t = now()
            await src.delete()
            created.remove(src)
            step("delete_source", s=round(now() - t, 2))

            # Restore copy A after the source is gone.
            t = now()
            a = await client.runtimes.create(f"{TAG}-a", checkpoint_id=ckpt_id)
            created.append(a)
            step("restore_a", s=round(now() - t, 2), runtime_id=a.id, info=str(await a.refresh_info()))
            await inspect(a, "copy_a")
            step("write_a_only", result=await sh(a, "echo only-in-a > /root/a-only.txt"))

            # Restore copy B: second restore of the same checkpoint (timing + isolation).
            t = now()
            b = await client.runtimes.create(f"{TAG}-b", checkpoint_id=ckpt_id)
            created.append(b)
            step("restore_b", s=round(now() - t, 2), runtime_id=b.id)
            await inspect(b, "copy_b")
        except Exception as e:
            step("fatal", error=f"{type(e).__name__}: {e}")
        finally:
            for rt in list(created):
                t = now()
                try:
                    await rt.delete()
                    step("cleanup_runtime", name=rt.name, s=round(now() - t, 2))
                except Exception as e:
                    step("cleanup_runtime_failed", name=rt.name, error=str(e))
            if ckpt_id:
                try:
                    await client.checkpoints.delete(ckpt_id)
                    step("cleanup_checkpoint", id=ckpt_id)
                except Exception as e:
                    step("cleanup_checkpoint_failed", id=ckpt_id, error=str(e))
    print(json.dumps(LOG, indent=2, default=str))


asyncio.run(main())
