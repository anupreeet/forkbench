#!/usr/bin/env bash
# Careful re-test of auto-suspend + wake-up (Preview feature). Records every request and status.
set -uo pipefail
cd "$(dirname "$0")/.."
set -a; . ./.env; set +a
TS=$(date +%s); RT="fb-wake-$TS"; LOG=${1:-evidence/12-wake-probe.log}; : > "$LOG"
log() { printf '[%s] %s\n' "$(date -u +%H:%M:%S)" "$*" | tee -a "$LOG"; }
status() { runta inspect "$RT" | jq -r '.. | .status? // empty' | head -1; }
trap 'runta rm "$RT" >/dev/null 2>&1; log "cleanup: runtimes named $RT left: $(runta ps -a | jq -c "[.runtimes[]? | .display_name // .name | select(.==\"$RT\")]")"' EXIT

log "runta $(runta --version | jq -r .version)"
log "\$ runta run --name $RT --cpus 1 --memory 1024 -p 8080/https --idle-mode suspend-and-wakeup --idle-timeout 30 --wait"
runta run --name "$RT" --cpus 1 --memory 1024 -p 8080/https --idle-mode suspend-and-wakeup --idle-timeout 30 --wait \
  | jq -c '{status: (.. | .status? // empty), idle: (.. | .idle_policy? // empty)}' 2>/dev/null | head -2 | tee -a "$LOG"
runta exec "$RT" -- bash -lc "mkdir -p /srv && echo hello-wake > /srv/index.html && cd /srv && setsid -f python3 -m http.server 8080 >/dev/null 2>&1; sleep 1" >/dev/null
URL=$(runta ports ls "$RT" | jq -r '.ports[0].url')/
log "URL from 'runta ports ls': $URL"
# Precondition: the server must be reachable through the public URL before we test wake-up.
ok=no
for i in $(seq 1 20); do
  inside=$(runta exec "$RT" -- bash -lc "pgrep -f 'http.server 8080' >/dev/null && curl -s -o /dev/null -w '%{http_code}' localhost:8080/ || echo DOWN" | jq -r '.stdout // .')
  pub=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 "$URL")
  log "precondition try $i: inside=$inside public=$pub"
  [ "$pub" = 200 ] && { ok=yes; break; }
  sleep 5
done
[ "$ok" = yes ] || { log "TEST INVALID: server never reachable via $URL before idling"; exit 1; }
log "inspect idle policy: $(runta inspect "$RT" | jq -c '.. | .idle_policy? // empty' | head -1)"

start=$(date +%s)
while [ $(( $(date +%s) - start )) -lt 360 ]; do [ "$(status)" = suspended ] && break; sleep 10; done
log "status after $(( $(date +%s) - start )) s without traffic: $(status)"

log "--- wake attempts: one request every ~10 s for 3 min (max-time 120 s each)"
w0=$(date +%s)
while [ $(( $(date +%s) - w0 )) -lt 180 ]; do
  r=$(curl -s -o /tmp/fb-wake-body -w '%{http_code} %{time_total}s' --max-time 120 "$URL")
  log "t=+$(( $(date +%s) - w0 ))s GET -> $r body=$(head -c 20 /tmp/fb-wake-body | tr -d '\n') | status=$(status)"
  case "$r" in 200*) break;; esac
  sleep 10
done

if [ "$(status)" = suspended ]; then
  log "--- still suspended: manual resume"
  t=$(date +%s); runta resume "$RT" | jq -c '.error // {ok: true}' | tee -a "$LOG"
  i=0; while [ $i -lt 30 ] && [ "$(status)" != running ]; do sleep 2; i=$((i+1)); done
  log "status $(( $(date +%s) - t )) s after 'runta resume': $(status)"
  log "after resume GET -> $(curl -s -o /tmp/fb-wake-body -w '%{http_code} %{time_total}s' --max-time 60 "$URL") body=$(head -c 20 /tmp/fb-wake-body)"
  log "http.server process alive after resume: $(runta exec "$RT" -- bash -lc "pgrep -fa 'http.server' || echo NONE" | jq -r '.stdout // .' 2>/dev/null)"
fi
rm -f /tmp/fb-wake-body
