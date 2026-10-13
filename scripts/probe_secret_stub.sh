#!/usr/bin/env bash
# Secret stubs, re-run with a corrected leak check (CLI-first, like the docs). Uses a FAKE value.
# Checks: upstream receives the injected header; a path outside the rule doesn't; the value is not
# in the runtime's environment and no file under common dirs contains it (file hits are COUNTED).
# Cleans up runtime, rules and secret on exit. Usage: scripts/probe_secret_stub.sh evidence/<out>.log
set -uo pipefail
cd "$(dirname "$0")/.."
LOG=$1; : > "$LOG"
TS=$(date +%s); RT="fb-verify-secret-$TS"; SECRET="fb-verify-secret-$TS"
FAKE="fb-verify-not-a-real-secret-$TS"
log() { printf '[%s] %s\n' "$(date -u +%H:%M:%S)" "$*" | tee -a "$LOG"; }
cleanup() {
  for id in $(runta secret rule ls "$RT" 2>/dev/null | jq -r '.. | .id? // empty' 2>/dev/null); do runta secret rule rm "$id" >/dev/null 2>&1; done
  runta rm "$RT" >/dev/null 2>&1; runta secret delete "$SECRET" >/dev/null 2>&1
  log "cleanup: secrets matching fb-verify: $(runta secret list | jq -c '[.. | .name? // empty | select(startswith("fb-verify"))]')"
}
trap cleanup EXIT
log "runta $(runta --version | jq -r .version)"
runta run --name "$RT" --cpus 1 --memory 1024 --wait >/dev/null
printf '%s' "$FAKE" | runta secret set --value-stdin "$SECRET" >/dev/null 2>&1; log "secret set exit=$?"
runta secret rule set "$RT" --host httpbin.org --path '/headers' --secret "$SECRET" --header X-Fb-Demo --template 'Bearer ${secret}' >/dev/null 2>&1
log "rule set exit=$? (host httpbin.org, path /headers, header X-Fb-Demo)"
start=$(date +%s); seen=no
while [ $(( $(date +%s) - start )) -lt 120 ]; do
  body=$(runta exec "$RT" -- bash -lc "curl -s --max-time 8 https://httpbin.org/headers" 2>/dev/null | jq -r '.stdout // empty')
  if printf '%s' "$body" | grep -qF "$FAKE"; then seen=yes; break; fi
  sleep 3
done
log "upstream /headers received the injected value: $seen (after $(( $(date +%s) - start )) s)"
b2=$(runta exec "$RT" -- bash -lc "curl -s --max-time 8 https://httpbin.org/get" 2>/dev/null | jq -r '.stdout // empty')
printf '%s' "$b2" | grep -qF "$FAKE" && log "/get (outside the rule): value PRESENT" || log "/get (outside the rule): value absent"
# the value is passed base64-encoded, so the literal value never appears on the remote command line
B64=$(printf '%s' "$FAKE" | base64)
envhits=$(runta exec "$RT" -- bash -c "v=\$(echo $B64 | base64 -d); env | grep -cF -- \"\$v\"" 2>/dev/null | jq -r '.stdout // empty' | tr -d '\n ')
filehits=$(runta exec "$RT" -- bash -c "v=\$(echo $B64 | base64 -d); grep -rlsF -- \"\$v\" /root /etc /tmp /home /run /var/lib /opt 2>/dev/null | wc -l" 2>/dev/null | jq -r '.stdout // empty' | tr -d '\n ')
log "inside runtime: env lines containing the value: ${envhits:-?}; files containing it under /root /etc /tmp /home /run /var/lib /opt: ${filehits:-?}"
