#!/usr/bin/env bash
# Run the docs' own background-server example exactly as published (auto-suspend page, runta-demos
# skill) via the CLI, then check 5 s later whether the server is still there. Cleans up on exit.
# Usage: scripts/probe_docs_bg_example.sh evidence/<out>.log
set -uo pipefail
cd "$(dirname "$0")/.."
LOG=$1; : > "$LOG"; RT="fb-verify-docsbg-$(date +%s)"
log() { printf '[%s] %s\n' "$(date -u +%H:%M:%S)" "$*" | tee -a "$LOG"; }
trap 'runta rm "$RT" >/dev/null 2>&1; log "cleanup: $(runta ps -a | jq -c "[.runtimes[]? | .display_name // .name | select(. == \"$RT\")]")"' EXIT
log "runta $(runta --version | jq -r .version)"
runta run --name "$RT" --cpus 1 --memory 1024 --wait >/dev/null || { log "create failed"; exit 1; }
log "\$ runta exec $RT -- sh -lc 'nohup python3 -m http.server 8080 >/tmp/http.log 2>&1 &'"
runta exec "$RT" -- sh -lc 'nohup python3 -m http.server 8080 >/tmp/http.log 2>&1 &' >/dev/null 2>&1; log "exit=$?"
sleep 5
log "5 s later, process: $(runta exec "$RT" -- bash -lc "pgrep -fa '[h]ttp.server 8080' || echo NONE" | jq -r '.stdout // empty' | tr '\n' ' ')"
log "5 s later, GET http://127.0.0.1:8080/ inside the runtime: $(runta exec "$RT" -- bash -lc "curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:8080/ || true" | jq -r '.stdout // empty')"
log "control: same server via setsid -f"
runta exec "$RT" -- sh -lc 'setsid -f python3 -m http.server 8081 >/tmp/http2.log 2>&1 < /dev/null' >/dev/null 2>&1
sleep 5
log "5 s later, GET http://127.0.0.1:8081/ (setsid -f): $(runta exec "$RT" -- bash -lc "curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:8081/ || true" | jq -r '.stdout // empty')"
