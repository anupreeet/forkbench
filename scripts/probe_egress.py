"""Time-resolved egress probe: after each policy change, how long until traffic follows it?

For each transition we set the policy, read it back, then probe every ~2 s (up to 150 s) until the
observed reachability matches the expectation. Every sample is recorded with its offset in seconds.
"""

import asyncio
import json
import time
from datetime import UTC, datetime

from runta import AsyncRunta, EgressMode, EgressPolicy

TAG = f"fb-egress-{int(time.time())}"
URLS = ["https://example.com", "https://pypi.org", "https://1.1.1.1", "https://1.0.0.1"]
PROBE = " ; ".join(
    f"printf '%s ' $(curl -s -o /dev/null -w '%{{http_code}}' --max-time 5 {u} || true)" for u in URLS
)
LIMIT_S = 150


def allow(*hosts):
    return EgressPolicy(mode=EgressMode.ALLOWLIST, allowed_hosts=list(hosts), denied_hosts=[])


# (label, policy, expectation per URL: True=reachable, False=blocked, None=don't care)
TRANSITIONS = [
    ("allow example.com", allow("example.com"), [True, False, False, False]),
    ("open (reset)", EgressPolicy(), [True, True, True, True]),
    ("allow nothing", allow(), [False, False, False, False]),
    ("allow IP 1.1.1.1", allow("1.1.1.1"), [False, False, True, False]),
    ("allow CIDR 1.0.0.0/24", allow("1.0.0.0/24"), [False, False, False, True]),
]


def reachable(code: str) -> bool:
    return code[:1] in "2345" and code != "000"


async def sample(rt):
    r = await rt.exec(["bash", "-lc", PROBE], timeout=60)
    codes = r.stdout_text.split()
    return codes + ["?"] * (len(URLS) - len(codes))


async def main():
    out = {"tag": TAG, "started_utc": datetime.now(UTC).isoformat(), "urls": URLS, "transitions": []}
    async with AsyncRunta() as client:
        rt = await client.runtimes.create(TAG, vcpus=2, memory_mib=4096)
        out["runtime_id"] = rt.id
        try:
            out["baseline"] = await sample(rt)
            for label, policy, expect in TRANSITIONS:
                t0 = time.monotonic()
                await rt.egress.set_policy(policy)
                set_s = time.monotonic() - t0
                read_back = str(await rt.egress.get())
                samples, converged_at = [], None
                while time.monotonic() - t0 < LIMIT_S:
                    codes = await sample(rt)
                    at = round(time.monotonic() - t0, 1)
                    samples.append({"t": at, "codes": codes})
                    ok = all(e is None or reachable(c) == e for c, e in zip(codes, expect, strict=True))
                    if ok:
                        converged_at = at
                        break
                    await asyncio.sleep(2)
                entry = {"label": label, "set_call_s": round(set_s, 2), "read_back": read_back,
                         "expect_reachable": expect, "converged_after_s": converged_at,
                         "n_samples": len(samples), "samples": samples}
                out["transitions"].append(entry)
                print(json.dumps({k: entry[k] for k in ("label", "converged_after_s", "n_samples")}),
                      flush=True)
        finally:
            await rt.delete()
    with open("evidence/06-egress-propagation.json", "w") as fh:
        json.dump(out, fh, indent=2, default=str)


asyncio.run(main())
