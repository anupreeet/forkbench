#!/usr/bin/env bash
# Final verification: re-run every reproduction fresh, record versions + timestamps.
# Part 1 is local (free). Part 2 uses one small runtime (~1 cent) and always cleans up.
# Output: evidence/99-final-verify-<UTC date>.log
set -uo pipefail
cd "$(dirname "$0")/.."
set -a; . ./.env; set +a
OUT="evidence/99-final-verify-$(date -u +%Y%m%dT%H%M%SZ).log"
S=$(mktemp -d)
RT="fb-final-$(date +%s)"
log() { printf '%s\n' "$*" | tee -a "$OUT"; }
cleanup() { runta rm "$RT" >/dev/null 2>&1; [ -n "${S:-}" ] && [ -d "$S" ] && rm -rf -- "${S:?}"; log "## cleanup: runtimes=$(runta ps -a | jq -c '[.runtimes[]? | .display_name // .name]') checkpoints=$(runta checkpoint ls | jq -c '[.checkpoints[]? | .display_name]') images=$(runta image ls | jq -c '[.images[]? | .name]')"; }
trap cleanup EXIT

log "# Final verification $(date -u +%Y-%m-%dT%H:%M:%SZ) | runta CLI $(runta --version | jq -r .version) | runta-sdk $(.venv/bin/python -c 'import importlib.metadata as m;print(m.version("runta-sdk"))') | $(sw_vers -productName) $(sw_vers -productVersion) $(uname -m)"

log ""; log "## L-01 documented install of the OpenAI Agents integration (fresh Python 3.12 venv)"
/opt/homebrew/bin/python3.12 -m venv "$S/oa" && "$S/oa/bin/python" -m pip install -q openai-agents-runta 2>&1 | tail -2 | tee -a "$OUT"

log ""; log "## L-03 pip install runta-sdk on macOS system Python ($(/usr/bin/python3 --version 2>&1))"
/usr/bin/python3 -m venv "$S/py39" && "$S/py39/bin/pip" install -q --disable-pip-version-check runta-sdk 2>&1 | tail -2 | tee -a "$OUT"

log ""; log "## L-14 / L-16 SDK create() keyword support"
.venv/bin/python - <<'EOF' 2>&1 | tee -a "$OUT"
import inspect
from runta import AsyncRuntimeManager
params = inspect.signature(AsyncRuntimeManager.create).parameters
print("create() has 'image':", "image" in params, "| has 'runtime_agent':", "runtime_agent" in params)
EOF

log ""; log "## L-18 subcommand --help with non-TTY stdout (expect command.name == subcommand)"
for sub in run exec checkpoint egress; do log "runta $sub --help -> command.name=$(runta $sub --help 2>/dev/null | jq -r .command.name)"; done
log "runta help run -> command.name=$(runta help run | jq -r .command.name)"

log ""; log "## L-20 runta checkpoint ls --all (documented flag)"
runta checkpoint ls --all 2>&1 | jq -c '.error | {code, message: (.message|split("\n")[0])}' | tee -a "$OUT"

log ""; log "## L-24 JSON help says --wait takes true|false; CLI rejects a value"
runta help run | jq -c '.command.args[] | select(.long=="wait") | {possible_values, value_names}' | tee -a "$OUT"
runta run --name fb-never-created --wait true 2>&1 | jq -c '.error | {code, message: (.message|split("\n")[0])}' | tee -a "$OUT"

log ""; log "## L-25 documented secret template \${credential} (validated client-side, no secret needed)"
runta secret rule set fb-nonexistent-runtime --host example.com --secret fb-nonexistent --header X-A --template 'Bearer ${credential}' 2>&1 | jq -c '.error' | tee -a "$OUT"

log ""; log "## L-02 public links published by Runta packages"
log "npm repository/homepage: $(npm view @runta/runta-sdk repository.url homepage 2>/dev/null | tr '\n' ' ')"
for u in https://github.com/runta-dev/typescript-sdk https://github.com/runta-dev/runta; do log "$u -> HTTP $(curl -s -o /dev/null -w '%{http_code}' "$u")"; done

log ""; log "## Part 2: one runtime ($RT) for L-21, L-22, L-27, L-28"
runta run --name "$RT" --cpus 1 --memory 1024 -p 8080/https --idle-mode suspend-and-wakeup --idle-timeout 30 --wait >/dev/null
.venv/bin/python - "$RT" <<'EOF' 2>&1 | tee -a "$OUT"
import asyncio, statistics, sys, time
from runta import AsyncRunta
async def main(name):
    async with AsyncRunta() as c:
        rt = await c.runtimes.get(name)
        walls = []
        for _ in range(20):
            t = time.monotonic(); await rt.exec(["true"]); walls.append(time.monotonic() - t)
        print(f"L-22 exec('true') x20: median {statistics.median(walls):.3f}s min {min(walls):.3f}s max {max(walls):.3f}s")
        for cmd in ("nohup sleep 700 >/dev/null 2>&1 &", "setsid -f sleep 701 >/dev/null 2>&1 < /dev/null"):
            await rt.exec(["bash", "-lc", cmd])
        await asyncio.sleep(5)
        r = await rt.exec(["bash", "-lc", "pgrep -fa 'sleep 70[01]' || echo NONE"])
        print("L-21 alive 5 s later:", r.stdout_text.strip().replace("\n", " | "))
        await rt.exec(["bash", "-lc", "mkdir -p /srv && echo hello-final > /srv/index.html && cd /srv && setsid -f python3 -m http.server 8080 >/dev/null 2>&1"])
asyncio.run(main(sys.argv[1]))
EOF
ID=$(runta inspect "$RT" | jq -r '.. | .id? // empty' | head -1)
URL=$(runta ports ls "$RT" | jq -r '.ports[0].url')
for i in $(seq 1 12); do [ "$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$URL/")" = 200 ] && break; sleep 5; done
log "L-27 (awake) ${URL}/ -> $(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$URL/")   |   https://$ID.runta.dev/ -> $(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "https://$ID.runta.dev/")"
st() { runta inspect "$RT" | jq -r '.. | .status? // empty' | head -1; }
t0=$(date +%s); while [ $(( $(date +%s) - t0 )) -lt 300 ] && [ "$(st)" != suspended ]; do sleep 10; done
log "L-28 status after $(( $(date +%s) - t0 )) s idle: $(st)"
for i in 1 2 3 4 5 6; do log "L-28 request $i while suspended -> $(curl -s -o /dev/null -w '%{http_code} %{time_total}s' --max-time 120 "$URL/") | status=$(st)"; sleep 10; done
