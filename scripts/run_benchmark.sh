#!/usr/bin/env bash
# Full benchmark: 4 tasks (29 MB → 1.25 GB images), each in its own process so tasks run in
# parallel, while trials *within* a task stay sequential and interleaved (clean timings).
# Usage: scripts/run_benchmark.sh [trials]   (default 5)   — needs RUNTA_TOKEN in env or .env
set -euo pipefail
cd "$(dirname "$0")/.."

TRIALS="${1:-5}"
# Compressed image sizes: 29 MB, 216 MB, 290 MB, 1249 MB.
# qemu-startup is timed but not graded: its verifier's `apt-get install curl` currently 404s upstream
# (Debian bullseye-security pool files missing; reproducible outside Runta, see evidence/10-qemu-verifier-404.log).
TASKS=(regex-log chess-best-move build-cython-ext qemu-startup)
OUT=results/bench
mkdir -p "$OUT"

pids=()
for t in "${TASKS[@]}"; do
  grade=--grade; [ "$t" = qemu-startup ] && grade=--no-grade
  .venv/bin/forkbench run "tasks/tb2-sample/sample/$t" \
      --trials "$TRIALS" --budget 4 "$grade" --out-dir "$OUT" > "$OUT/$t.console.log" 2>&1 &
  pids+=($!)
done

status=0
for p in "${pids[@]}"; do wait "$p" || status=1; done

cat "$OUT"/raw/*.jsonl > "$OUT/all.jsonl"
.venv/bin/forkbench report "$OUT/all.jsonl" --out-dir "$OUT"
exit "$status"
