"""Probe session: one runtime, several small questions. Everything is logged; cleanup is guaranteed.

Q1 exec round-trip: 20x `true`, our wall time vs Runta's reported duration.
Q2 background processes (L-21): which ways of backgrounding survive the end of an exec call?
Q3 egress (L-11): are IPs / CIDRs accepted in an allowlist, and do they work as expected?
"""

import asyncio
import json
import statistics
import subprocess
import time
from datetime import UTC, datetime

from runta import AsyncRunta, EgressMode, EgressPolicy

TAG = f"fb-probe-{int(time.time())}"
OUT: dict = {"tag": TAG, "started_utc": datetime.now(UTC).isoformat()}


async def sh(rt, cmd, timeout=120):
    t = time.monotonic()
    r = await rt.exec(["bash", "-lc", cmd], timeout=timeout)
    return {"cmd": cmd, "exit": r.exit_code, "wall_s": round(time.monotonic() - t, 3),
            "server_s": getattr(r, "duration", None), "out": r.stdout_text.strip()[-600:],
            "err": r.stderr_text.strip()[-300:]}


CURL = "curl -s -o /dev/null -w '%{{http_code}}' --max-time 8 {url}; echo \" exit=$?\""


async def reach(rt):
    return {u: (await sh(rt, CURL.format(url=u)))["out"]
            for u in ("https://example.com", "https://1.1.1.1", "https://pypi.org")}


async def main():
    async with AsyncRunta() as client:
        t = time.monotonic()
        rt = await client.runtimes.create(TAG, vcpus=2, memory_mib=4096)
        OUT["create_s"] = round(time.monotonic() - t, 2)
        OUT["runtime_id"] = rt.id
        try:
            # Q1
            samples = [await sh(rt, "true") for _ in range(20)]
            walls = [s["wall_s"] for s in samples]
            servers = [s["server_s"] for s in samples if isinstance(s["server_s"], (int, float))]
            OUT["q1_exec_true_x20"] = {
                "wall_median_s": statistics.median(walls), "wall_min_s": min(walls),
                "wall_max_s": max(walls),
                "server_duration_median_s": statistics.median(servers) if servers else None,
                "server_duration_type": type(samples[0]["server_s"]).__name__,
            }

            # Q2
            OUT["q2_pid1"] = (await sh(rt, "ps -p 1 -o comm=; ps -o pid,pgid,sid,comm -p $$"))["out"]
            variants = {
                "nohup_amp": "nohup sleep 600 >/dev/null 2>&1 &",
                "nohup_amp_disown": "nohup sleep 601 >/dev/null 2>&1 & disown",
                "setsid": "setsid nohup sleep 602 >/dev/null 2>&1 < /dev/null &",
                "setsid_fork": "setsid -f sleep 603 >/dev/null 2>&1 < /dev/null",
                "systemd_run": "systemd-run --unit=fbprobe sleep 604 2>&1 || echo NO_SYSTEMD",
            }
            OUT["q2_start"] = {k: await sh(rt, v) for k, v in variants.items()}
            await asyncio.sleep(3)
            OUT["q2_alive_after_3s"] = (await sh(rt, "pgrep -fa 'sleep 60[0-4]' || echo NONE"))["out"]
            await asyncio.sleep(15)
            OUT["q2_alive_after_18s"] = (await sh(rt, "pgrep -fa 'sleep 60[0-4]' || echo NONE"))["out"]

            # Q3
            OUT["q3_default_open"] = await reach(rt)
            cases = {
                "allow_hostname_example.com": ["example.com"],
                "allow_ip_1.1.1.1": ["1.1.1.1"],
                "allow_cidr_1.1.1.0/24": ["1.1.1.0/24"],
                "allow_empty_list": [],
            }
            q3 = {}
            for label, hosts in cases.items():
                entry: dict = {"allowed_hosts": hosts}
                try:
                    await rt.egress.set_policy(EgressPolicy(mode=EgressMode.ALLOWLIST,
                                                            allowed_hosts=hosts, denied_hosts=[]))
                    entry["accepted"] = True
                    entry["policy_read_back"] = str(await rt.egress.get())
                    entry["reach"] = await reach(rt)
                except Exception as e:  # record rejections verbatim
                    entry["accepted"] = False
                    entry["error"] = f"{type(e).__name__}: {e}"[:400]
                q3[label] = entry
            OUT["q3_sdk"] = q3
            await rt.egress.set_policy(EgressPolicy())
            OUT["q3_reset_open"] = await reach(rt)
            cli = {}
            for label, arg in {"ip": "1.1.1.1", "cidr": "1.1.1.0/24"}.items():
                p = subprocess.run(["runta", "egress", "set", TAG, "--mode", "allowlist", "--allow", arg],
                                   capture_output=True, text=True, timeout=60)
                cli[label] = {"exit": p.returncode, "stdout": p.stdout[-500:], "stderr": p.stderr[-300:]}
            OUT["q3_cli"] = cli
        finally:
            t = time.monotonic()
            await rt.delete()
            OUT["delete_s"] = round(time.monotonic() - t, 2)
    print(json.dumps(OUT, indent=2, default=str))


asyncio.run(main())
