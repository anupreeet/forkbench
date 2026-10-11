#!/usr/bin/env bash
# Feature tour (breadth): secret stubs, public URL + auto-suspend/wake. CLI-first, like the docs.
# Uses a FAKE secret value. Cleans up runtimes, rules and the secret on exit (trap).
set -uo pipefail
cd "$(dirname "$0")/.."
set -a; . ./.env; set +a

TS=$(date +%s)
SEC_RT="fb-tour-sec-$TS"
WEB_RT="fb-tour-web-$TS"
SECRET_NAME="fb-tour-demo-$TS"
FAKE_VALUE="fb-demo-not-a-real-secret-$TS"
LOG=evidence/09-tour.log
: > "$LOG"

log() { printf '[%s] %s\n' "$(date -u +%H:%M:%S)" "$*" | tee -a "$LOG"; }
run() { log "\$ $*"; "$@" 2>&1 | tee -a "$LOG"; return "${PIPESTATUS[0]}"; }

cleanup() {
  log "--- cleanup"
  for id in $(runta secret rule ls "$SEC_RT" 2>/dev/null | jq -r '.. | .id? // empty' 2>/dev/null); do
    runta secret rule rm "$id" >/dev/null 2>&1 || true
  done
  runta rm "$SEC_RT" "$WEB_RT" >/dev/null 2>&1 || true
  runta secret delete "$SECRET_NAME" >/dev/null 2>&1 || true
  log "runtimes left: $(runta ps -a | jq -c '[.runtimes[]? | .display_name // .name]')"
  log "secrets left matching fb-tour: $(runta secret list | jq -c '[.. | .name? // empty | select(startswith("fb-tour"))]')"
}
trap cleanup EXIT

########## Part A: secret stubs ##########
log "=== A. secret stubs (fake value; never printed by us)"
run runta run --name "$SEC_RT" --cpus 1 --memory 1024 --wait >/dev/null
printf '%s' "$FAKE_VALUE" | runta secret set --value-stdin "$SECRET_NAME" > /tmp/fb-secret-set.json 2>&1
log "secret set exit=$? (output redacted: $(jq -c 'del(.. | .value?)' /tmp/fb-secret-set.json 2>/dev/null | head -c 300))"
rm -f /tmp/fb-secret-set.json

log "A1: rule exactly as in the docs: --template 'Bearer \${credential}'"
run runta secret rule set "$SEC_RT" --host httpbin.org --path '/headers' --secret "$SECRET_NAME" \
    --header X-Fb-Demo --template 'Bearer ${credential}'
log "A2: rule as the CLI help describes: --template 'Bearer \${secret}'"
run runta secret rule set "$SEC_RT" --host httpbin.org --path '/headers' --secret "$SECRET_NAME" \
    --header X-Fb-Demo --template 'Bearer ${secret}'
run runta secret rule ls "$SEC_RT"

log "A3: does the injected header reach the upstream? (poll up to 120 s; httpbin echoes headers)"
start=$(date +%s); seen=no
while [ $(( $(date +%s) - start )) -lt 120 ]; do
  body=$(runta exec "$SEC_RT" -- bash -lc "curl -s --max-time 8 https://httpbin.org/headers" 2>/dev/null | jq -r '.stdout // .output // empty' 2>/dev/null)
  if printf '%s' "$body" | grep -q "$FAKE_VALUE"; then seen=yes; break; fi
  sleep 3
done
log "injected value seen by upstream: $seen after $(( $(date +%s) - start )) s"
log "A4: path scoping: /get is not covered by the rule -> header should be absent"
b2=$(runta exec "$SEC_RT" -- bash -lc "curl -s --max-time 8 https://httpbin.org/get" 2>/dev/null | jq -r '.stdout // empty')
printf '%s' "$b2" | grep -q "$FAKE_VALUE" && log "/get: VALUE PRESENT (unexpected)" || log "/get: value absent (expected)"
log "A5: is the value visible inside the runtime? (env + common dirs)"
inside=$(runta exec "$SEC_RT" -- bash -lc "env; grep -rsl -- '$FAKE_VALUE' /root /etc /tmp /home 2>/dev/null | head -5" 2>/dev/null | jq -r '.stdout // empty')
printf '%s' "$inside" | grep -q "$FAKE_VALUE" && log "inside runtime: FOUND" || log "inside runtime: not found in env or files"

########## Part B: public URL + auto-suspend / wake ##########
log "=== B. publish + auto-suspend/wake"
run runta run --name "$WEB_RT" --cpus 1 --memory 1024 -p 8080/https \
    --idle-mode suspend-and-wakeup --idle-timeout 30 --wait > /tmp/fb-web.json
WEB_ID=$(runta inspect "$WEB_RT" | jq -r '.. | .id? // empty' | head -1)
log "web runtime id: $WEB_ID"
run runta exec "$WEB_RT" -- bash -lc "mkdir -p /srv && echo hello-forkbench > /srv/index.html && cd /srv && setsid -f python3 -m http.server 8080 >/dev/null 2>&1; sleep 1; curl -s localhost:8080/"
run runta ports ls "$WEB_RT"
for url in "https://$WEB_ID.runta.dev/" "https://8080-$WEB_ID.runta.dev/"; do
  r=$(curl -s -o /tmp/fb-body -w '%{http_code} %{time_total}' --max-time 20 "$url"); log "GET $url -> $r body=$(head -c 40 /tmp/fb-body)"
done
log "B2: no traffic now; poll status until suspended (max 360 s)"
start=$(date +%s); st=""
while [ $(( $(date +%s) - start )) -lt 360 ]; do
  st=$(runta inspect "$WEB_RT" | jq -r '.. | .status? // empty' | head -1)
  [ "$st" = "suspended" ] && break
  sleep 15
done
log "status after $(( $(date +%s) - start )) s idle: $st"
for url in "https://$WEB_ID.runta.dev/" "https://8080-$WEB_ID.runta.dev/"; do
  r=$(curl -s -o /tmp/fb-body -w '%{http_code} %{time_total}' --max-time 120 "$url"); log "WAKE GET $url -> $r body=$(head -c 40 /tmp/fb-body)"
done
log "status after wake request: $(runta inspect "$WEB_RT" | jq -r '.. | .status? // empty' | head -1)"
rm -f /tmp/fb-body /tmp/fb-web.json
