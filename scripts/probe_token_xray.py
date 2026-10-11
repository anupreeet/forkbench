"""Token X-Ray without spending: does capture record tool I/O from an OpenAI-format request?

Creates a runtime with --llm-tool-io-capture (CLI only; SDKs lack the option, finding L-30), then
sends ONE chat-completions request with a deliberately wasteful tool result (a 300-row JSON array
with repeated keys) and a FAKE API key. OpenAI rejects it (401), so nothing is billed anywhere.
Then reads `runta tokens analyze --runtime <name>`. A negative result is inconclusive (capture may
require a successful upstream response), so only a positive result supports a claim.
"""

import asyncio
import json
import subprocess
import time
from datetime import UTC, datetime

from runta import AsyncRunta

NAME = f"fb-xray-{int(time.time())}"
rows = [{"id": i, "status": "ok", "region": "us-east-1", "owner": "team-data"} for i in range(300)]
BODY = {
    "model": "gpt-4o-mini",
    "messages": [
        {"role": "user", "content": "List the jobs."},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "call_1", "type": "function",
            "function": {"name": "list_jobs", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "call_1", "content": json.dumps(rows)},
    ],
    "tools": [{"type": "function", "function": {"name": "list_jobs", "parameters": {"type": "object", "properties": {}}}}],
}


def cli(*args: str) -> dict:
    p = subprocess.run(["runta", *args], capture_output=True, text=True, timeout=600)
    try:
        return json.loads(p.stdout)
    except json.JSONDecodeError:
        return {"raw": (p.stdout + p.stderr)[-500:], "exit": p.returncode}


async def main():
    out = {"name": NAME, "started_utc": datetime.now(UTC).isoformat()}
    cli("run", "--name", NAME, "--cpus", "1", "--memory", "1024", "--llm-tool-io-capture", "--wait")
    out["capture_flag_on_runtime"] = next((v for k, v in _walk(cli("inspect", NAME)) if k == "llm_tool_io_capture_enabled"), None)
    async with AsyncRunta() as c:
        rt = await c.runtimes.get(NAME)
        try:
            await rt.files.write("/tmp/body.json", json.dumps(BODY))
            r = await rt.exec(["bash", "-lc", "curl -s -o /tmp/resp.json -w '%{http_code}' https://api.openai.com/v1/chat/completions "
                                               "-H 'Authorization: Bearer sk-fake-key-for-capture-test' -H 'Content-Type: application/json' "
                                               "--data-binary @/tmp/body.json; echo; head -c 200 /tmp/resp.json"], timeout=60)
            out["upstream"] = r.stdout_text.strip()[:300]
            for wait_s in (15, 30, 60, 90):
                await asyncio.sleep(wait_s - (0 if wait_s == 15 else {30: 15, 60: 30, 90: 60}[wait_s]))
                res = cli("tokens", "analyze", "--runtime", NAME)
                out[f"analyze_after_{wait_s}s"] = res.get("summary", res)
                if res.get("calls"):
                    out["first_call"] = res["calls"][0]
                    break
        finally:
            await rt.delete()
    print(json.dumps(out, indent=2, default=str))


def _walk(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k, v
            yield from _walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v)


asyncio.run(main())
