# forkbench: build once, fork many

**How fast can an RL task environment be reset on Runta, and does it stay correct?**

Agentic RL runs the same task many times, and every attempt needs a fresh, clean, *ready* environment.
On Runta today, Harbor's Runta backend (v0.24.0) creates a new runtime for every trial and, for tasks
that ship a Docker image, **pulls that image every time**. Runta has a primitive that should make this cheaper: **checkpoints**, which preserve
the filesystem *and running processes*. forkbench measures both paths on real Terminal-Bench tasks and
checks that every forked copy is correct and isolated.

<!-- RESULTS:START -->
## Results (2026-10-11, 56 trials, ~$1.14 of runtime)

**Restoring a ready environment from a checkpoint cut the per-trial reset by 22–61% (median per task),
and the one-off "golden" build (~4 min) paid for itself after 1.8–8.5 trials.** For 3 of the 4 tasks the
two strategies' ranges don't overlap; for regex-log they do, so its 25% is indicative only. Every forked
copy started clean, with its task container still running.

| task (image) | reset from scratch | reset from checkpoint | saved per trial | one-off golden build | break-even |
|---|---|---|---|---|---|
| regex-log (29 MB) | 118.8s (110.4–168.7, n=5) | 89.5s (76.8–130.0, n=5) | 29s (25%) | 219s | 7.5 trials |
| chess-best-move (216 MB) | 209.3s (144.1–232.1, n=5) | 81.6s (75.6–97.9, n=4) | 128s (61%) | 227s | 1.8 trials |
| build-cython-ext (290 MB) | 140.8s (136.9–216.2, n=5) | 78.3s (71.9–124.7, n=4) | 62s (44%) | 227s | 3.6 trials |
| qemu-startup (1249 MB) | 141.4s (131.4–157.3, n=5) | 110.6s (73.3–128.3, n=5) | 31s (22%) | 263s | 8.5 trials |

`reset` is the time until the task container accepts commands: create or restore + image pull + container start.
Values are median (min–max, n = oracle trials per cell). Runtimes were 2 vCPU / 4 GiB, and strategies
were interleaved per task.
Full table: [`results/bench/all/summary.md`](results/bench/all/summary.md). Chart:
[`results/bench/all/report.html`](results/bench/all/report.html).

What the numbers say:
- **The win is skipping setup, not faster machines.** A checkpoint restore provisioned no faster than
  a fresh create (medians 75–108 s vs 75–95 s). The savings are the image pull and container start a
  restore skips, which is why `chess-best-move` (slow pull) gained most. `qemu-startup`'s 1.25 GB image
  pulled quickly, and its restores happened to provision slowly, so it gained least. The savings
  therefore depend on how fast the image pulls from the runtime.
- **Forks are clean and isolated.** 25/25 checkpoint forks had no state from earlier forks, 0/25
  needed their container restarted, and clock skew stayed ≤ 0.35 s. Forks of the same checkpoint share `machine-id`/`boot_id` (4 unique values
  for 4 checkpoints; expected for snapshot clones) but drew unique fresh kernel UUIDs (25/25).
- **A network policy given at restore applies immediately; changing it later doesn't.** Deny-all
  passed to the restore call was enforced at the first probe in 4/4 tasks (host and container), while
  later policy changes took ~20–30 s to be enforced ([FEEDBACK](FEEDBACK.md) bad #1).
- **Graded correctness is thin, and here's why.** 30 of 33 verifier runs on the three gradable tasks
  couldn't run their tests because the verifier's `uv` download from GitHub failed ([FEEDBACK](FEEDBACK.md)
  bad #3). `qemu-startup` isn't gradable right now (its verifier hits an upstream Debian 404). Only 3 trials
  were graded. The regex-log checkpoint fork was correct (reward 1). The two build-cython-ext runs scored 0
  because the task's reference solution itself failed: `solve.sh` exited with an error in all 9 completed
  runs, under both strategies, so those zeros say nothing about forks. That `solve.sh` starts with a
  `git clone` from github.com, so it may be the same GitHub problem as FEEDBACK bad #3, but its output
  wasn't recorded. One more regex-log checkpoint fork
  was graded correct in the earlier smoke run.
- **Third option, Runtime Images (CLI only):** a runtime booted from a custom image built `FROM` the
  regex-log task image was ready in 71.8 s (median, n=3), with no Docker step. It's competitive with checkpoints,
  but neither SDK can create it ([FEEDBACK](FEEDBACK.md) bad #4).
- 3 checkpoint-arm trials failed together at 04:22Z, when calls to Runta (including plain REST calls)
  failed from my machine. Cause unknown, possibly my network; they're excluded as errors.
<!-- RESULTS:END -->

## What it does

```
scratch      (per trial)  create runtime ─► docker pull task image ─► start container ─► run trial ─► delete
checkpoint   (once)       create runtime ─► docker pull ─► start container ─► checkpoint ─► delete builder
             (per trial)  restore runtime from checkpoint ─► (container is already running) ─► run trial ─► delete
```

Each **trial** runs the same workload as Harbor's `oracle` / `nop` agents and verifier:
- upload `solution/`, then run `solve.sh` (oracle only)
- upload `tests/`, run `test.sh`, read `/logs/verifier/reward.txt`
- expected reward: oracle = 1, nop = 0

Every trial also checks that the fork is trustworthy:

| Check | Pass condition |
|---|---|
| Correctness | reward matches the expectation; graded only if the verifier actually ran (pytest summary present) |
| No leftover state | marker files written by earlier forks are absent |
| Clock | skew vs local UTC ≤ round-trip time |
| Egress policy | deny-all blocks real requests from the host (HTTPS) and the container (HTTP, gateway `403`); open restores real page content; polled until enforced; timeouts reported separately from blocks |
| Identity (recorded only) | boot_id / machine-id / hostname / fresh kernel UUID across forks |

## Quick start

```bash
uv sync                                   # Python 3.12+, installs runta-sdk 0.2.1
cp .env.example .env                      # put RUNTA_TOKEN=... in .env (a scoped key that can read + manage runtimes)
git clone https://github.com/laude-institute/terminal-bench-2-0-sample tasks/tb2-sample
git -C tasks/tb2-sample checkout 7e917f35c281188532772312d4ad91ca9274febc   # pinned

uv run forkbench doctor                                    # python, sdk, token present, API reachable
uv run forkbench run tasks/tb2-sample/sample/regex-log --trials 3 --budget 2
open results/<run_id>/report.html
uv run forkbench cleanup --yes                             # safety net: deletes any fb-* leftovers
```

`scripts/run_benchmark.sh` reproduces the full benchmark (4 tasks in parallel, trials interleaved).

## Design choices

- **Baseline mirrors Harbor.** The scratch path copies Harbor 0.24.0's Runta "docker" mode step by step
  (`docker image inspect || docker pull`, then `docker create … sleep loop` and `docker start`). Both
  strategies use 2 vCPU / 4 GiB (Harbor's default is 1 vCPU / 2 GiB).
  I also ran `harbor run -e runta` itself on the same tasks. Its verifiers hit the GitHub download
  stall described in FEEDBACK, so that run gives no reward cross-check; the shared code path is the
  fairness argument.
- **Statistics.** Strategies alternate (A,B / B,A …) to cancel time-of-day drift. Results show
  median and min–max with *n*; no percentiles from five samples. The one-off cost of building the
  checkpoint is reported, along with the break-even number of trials.
- **Nothing left running.** Every runtime and checkpoint is named `fb-<run_id>-…`. A failed setup
  deletes its runtime immediately. The run ends with a shielded cleanup that survives Ctrl-C, and
  `forkbench cleanup` is the safety net. Unit tests simulate a failed setup, a failed delete and Ctrl-C
  mid-run, and check that nothing is left behind.
- **Budget guard.** The run is refused up front if the estimate exceeds `--budget`, and new trials stop
  once the running estimate reaches it.
- **Secrets.** The token is held as `SecretStr`, never printed, and redacted from logs. Use a scoped key
  that expires. Task code is untrusted: it only executes inside Runta runtimes, never locally.
- **No extra retry layer.** The Python SDK already retries create (with an idempotency key), patch and
  file transfers on 408/429/502/503/504 and timeouts, and delete on 5xx or connection errors. Exec only
  retries "runtime not found" while a runtime starts, so a dropped exec fails its trial and is reported
  as an error rather than hidden.

## Repository map

| Path | What |
|---|---|
| `src/forkbench/` | the tool (~1.3k non-blank lines): `sandbox.py` (Runta wrapper + cleanup), `strategies.py`, `workload.py` (trial + checks), `runner.py`, `report.py`, `cli.py` |
| `tests/` | 20 unit tests against an in-memory fake SDK (no network, no cost) |
| `results/` | raw JSONL per trial + `summary.md` + self-contained `report.html` |
| `FEEDBACK.md` | product feedback on Runta: the good, the bad, the ugly |
| `evidence/` | raw command output behind every feedback item |
| `scripts/` | `run_benchmark.sh`, `docs_lint.py` (checks every `runta …` line in the docs against the CLI), `final_verify.sh` (re-runs every reproduction), and the probes that produced the evidence |

## Limitations

- Small samples (n = 4–5 per cell), one client location, one time window. Treat the numbers as indicative.
- The SDK's `create()` silently retries transient errors (including 429) for up to 480 s, so a
  provisioning time could include retries that forkbench can't see.
- Prebuilt task images only. A third strategy, booting a runtime from a custom Runtime Image, is
  measured separately via the CLI (`scripts/probe_image_runtime.py`, n=3): neither Runta SDK can create
  a runtime from an image yet. See FEEDBACK.
- `qemu-startup` is timed but not graded. Its verifier currently fails upstream (Debian package 404s,
  reproducible outside Runta).

## Next step: make it a one-flag option in Harbor

Runta's FrontierHarness eval already captures each task environment as a "golden Runta checkpoint"
([blog](https://runta.com/blog/introducing-frontierharness-eval/), `provision-golden-checkpoint.sh`).
The same idea fits inside Harbor's `RuntaEnvironment` (`src/harbor/environments/runta.py`, v0.24.0),
which today creates a runtime and runs `docker pull` for every trial:

```python
# sketch: harbor run -e runta --ek reset=checkpoint
key = hash((docker_image or env_dir_digest, cpus, memory_mib))      # one golden per environment
async with golden_lock(key):                                       # N parallel trials → 1 build
    ckpt = find_checkpoint(f"harbor-golden-{key}") or await build_then_checkpoint(key)
runtime = await client.runtimes.create(name, checkpoint_id=ckpt.id,
                                       egress_policy=trial_policy)  # policy set at creation
```

- Delete the golden checkpoints when the job ends, or keep them with a TTL across jobs.
- Pass the trial's network policy **in the restore call**, not as a later `set_policy` (see FEEDBACK:
  policy changes on a running runtime took ~20–30 s to be enforced).
