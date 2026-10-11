#!/usr/bin/env bash
# Why do large GitHub release downloads stall inside runtimes? Same downloads, same time window:
#   A = default runtime (egress gateway with TLS interception)
#   B = runtime with --tls-passthrough-host for GitHub hosts (gateway does not intercept those)
#   M = this Mac (outside Runta) as a control
set -uo pipefail
cd "$(dirname "$0")/.."
set -a; . ./.env; set +a
TS=$(date +%s); A="fb-dl-a-$TS"; B="fb-dl-b-$TS"; LOG=evidence/15-download-probe.log; : > "$LOG"
log() { printf '[%s] %s\n' "$(date -u +%H:%M:%S)" "$*" | tee -a "$LOG"; }
trap 'runta rm "$A" "$B" >/dev/null 2>&1; log "cleanup done; left: $(runta ps -a | jq -c "[.runtimes[]? | .display_name // .name | select(startswith(\"fb-dl-\"))]")"' EXIT

UV=https://github.com/astral-sh/uv/releases/download/0.7.13/uv-x86_64-unknown-linux-gnu.tar.gz
CF='https://speed.cloudflare.com/__down?bytes=20000000'
FMT='code=%{http_code} size=%{size_download} time=%{time_total}s speed=%{speed_download}B/s'

log "runta $(runta --version | jq -r .version); creating A (default) and B (TLS passthrough for GitHub)"
runta run --name "$A" --cpus 1 --memory 1024 --wait >/dev/null &
runta run --name "$B" --cpus 1 --memory 1024 --wait \
  --tls-passthrough-host github.com --tls-passthrough-host '*.githubusercontent.com' >/dev/null &
wait
log "B config: $(runta inspect "$B" | jq -c '.. | .tls_passthrough_hosts? // empty' | head -1)"

remote() {  # $1 runtime, $2 label, $3 url
  local out
  out=$(runta exec "$1" -- bash -lc "curl -sSL -o /dev/null --max-time 90 -w '$FMT' '$3' 2>&1; echo \" exit=\$?\"" | jq -r '.stdout // .')
  log "$2 $out"
}
local_dl() { log "$1 $(curl -sSL -o /dev/null --max-time 90 -w "$FMT" "$2" 2>&1; echo " exit=$?")"; }

log "--- TLS issuer seen for github.com"
for rt in "$A" "$B"; do
  log "$rt: $(runta exec "$rt" -- bash -lc "curl -sv -o /dev/null https://github.com 2>&1 | grep -m1 'issuer:'" | jq -r '.stdout // .')"
done
log "M: $(curl -sv -o /dev/null https://github.com 2>&1 | grep -m1 'issuer:')"

for i in 1 2 3; do
  log "--- round $i: uv tarball (17.8 MB, GitHub release -> release-assets.githubusercontent.com)"
  remote "$A" "A uv#$i" "$UV" & remote "$B" "B uv#$i" "$UV" & local_dl "M uv#$i" "$UV" & wait
done
for i in 1 2; do
  log "--- round $i: 20 MB from speed.cloudflare.com (non-GitHub control)"
  remote "$A" "A cf#$i" "$CF" & remote "$B" "B cf#$i" "$CF" & local_dl "M cf#$i" "$CF" & wait
done
