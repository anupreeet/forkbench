# Evidence index

Raw outputs behind the claims in [FEEDBACK.md](../FEEDBACK.md) and the README results. Times are UTC.
The first round ran on 2026-10-10/11, and a re-verification round on 2026-10-13 (files with that date).
Versions: runta CLI 0.2.15, runta-sdk 0.2.1 (Python), @runta/runta-sdk 0.2.0 (TS), Harbor 0.24.0,
macOS 26.6.2 arm64. No API token appears in any file (redacted at source, scanned with `gitleaks`);
local paths are shown relative to the repo.

| File | What it shows | Backs |
|---|---|---|
| `env.txt` | tool versions | context |
| `02-smoke.json` | first runtime: OS, disk, Docker, timings | the good |
| `03-checkpoint-spike.*` | what a checkpoint preserves (files, Docker, a running container), restore after the source is deleted, sibling isolation | the good |
| `04-local-repros.log` | second reproductions: Python 3.9 install, `create(image=)`, `create(runtime_agent=)`, non-TTY `--help`, `checkpoint ls --all` | U3, U4, U6, U7, bad #4 |
| `05-probe.json` | exec latency (n=20), which background patterns survive an exec, IP/CIDR allowlists | bad #5, bad #6, the good |
| `05b-exec-latency-20261013.json` | exec split into WebSocket open vs start→exit (n=20), plus fresh HTTPS timings to the API | bad #5 |
| `05c-docs-background-example-20261013.log` | the docs' `nohup … &` server example run exactly as published, with a `setsid -f` control | bad #6 |
| `06-egress-propagation.json` | 5 policy changes on one runtime, every probe until enforced | bad #1, the good |
| `06b-egress-timing-20261013.*` | 6 more policy changes (host + container probes) | bad #1, the good |
| `07-harbor-*.log`, [`../jobs/`](../jobs/) | Harbor `-e runta` oracle/nop runs; verifiers fail on the GitHub `uv` download | bad #3 |
| `09-tour.log` | secret stubs, `${credential}` template rejection, public URL formats, auto-suspend | the good, U1, U9 |
| `09b-secret-stub-20261013.log` | secret stub re-run with a corrected on-disk leak check | the good |
| `10-qemu-verifier-404.log` | qemu-startup's verifier fails upstream (Debian 404, reproduced outside Runta) | not a Runta issue |
| `11-ts-sdk-list.log` | TypeScript SDK works; its create options | the good, bad #4 |
| `12b-wake-probe-fixed.log` | server reachable before idling; suspended runtime not woken by 16 requests; manual resume 95 s | bad #2 |
| `13-image-build.log`, `16-image-runtime*.json` | Runtime Image build, and runtime-from-image readiness (n=3) | the good, bad #4, README results |
| `15-download-probe.log` | same GitHub asset via gateway, TLS passthrough, and a local control; certificate issuer on each path | bad #3 |
| `17-egress-container-method.json` | deny-all blocks host and container (gateway 403); 2 more policy changes | the good, bad #1 |
| `18-container-download.progress.log`, `18b-*` | host-level GitHub download stalls (0 bytes in 150 s) while a 20 MB non-GitHub download completes; local control during the stall. The container columns used `curlimages/curl`, which fails TLS verification on Runta (see 25d). | bad #3, the good |
| `19-download-watch.json` | 14 paired rounds: default gateway vs TLS passthrough (one runtime each) | bad #3 |
| `19b-download-ab-20261013.*` | 10 rounds, two runtimes per arm, plus a local control | bad #3 |
| `19c-gateway-controls-20261013.log` | on the same default-path runtimes: non-GitHub downloads, github.com, api.github.com, and two more release downloads | bad #3 |
| `20-sdk-option-coverage.txt` | create-time options missing from both SDKs' public layer, with positive controls | bad #4 |
| `21-token-xray.json` | zero-cost Token X-Ray attempt (no capture) | not tested |
| `22-rest-errors.log`, `22b-rest-errors-20261013.log` | REST error envelopes (400, 401, 404, 422) and `request_id`; SDK error classes | the good, bad #7 |
| `23-pause-resume.run1.log`, `.run2.log`, `.run3-20261013.*` | pause → resume timings, state kept, 429 "no available compute capacity" | bad #2 |
| `24-docs-lint.txt`, `24b-*`, `24c-*` | docs linter output (17 hits, 3 false positives), then each real hit confirmed twice with a sentinel flag | U10–U13 |
| `25-container-vs-host.*`, `25b-container-vs-host-20261013.*` | gateway CA and `*_CA_BUNDLE` injected into the task container; host vs container GitHub downloads (the container `exit` field is unreliable there because of shell quoting; use code and size) | the good, bad #3 |
| `25c-ca-and-background-20261013.json` | CA injection and HTTPS in three more images; background patterns via SDK and CLI | the good, bad #6 |
| `25d-ca-nonroot-20261013.json` | `curlimages/curl` vs `python:3.12-slim`, as root and non-root, on two runtimes | the good |
| `26-stalled-runtime-download.log` | a stalled transfer doesn't recover while a new request succeeds (run on two benchmark runtimes during their verifier phase) | bad #3 |
| `L-*.log` | single-finding reproductions (see IDs below) | ugly |
| `99-final-verify-*.log` | full re-runs of every reproduction ([`../scripts/final_verify.sh`](../scripts/final_verify.sh)); in the 10-13 run, the runtime listed at cleanup belonged to another probe running in parallel, and the final account check was empty | all |

## Finding IDs

Scripts and logs tag findings with `L-xx` IDs. They map to FEEDBACK.md as follows:

| ID | FEEDBACK | ID | FEEDBACK |
|---|---|---|---|
| L-01 | U5 | L-21 | bad #6 |
| L-02 | U8 | L-22 | bad #5 |
| L-03 | U7 | L-24 | U2 |
| L-14, L-30 | bad #4 | L-25 | U1 |
| L-16 | U6 | L-27 | U9 |
| L-18 | U3 | L-28, L-33 | bad #2 |
| L-20 | U4 | L-29 | bad #3 |
| L-11 | the good (IPs/CIDRs) | L-31 | bad #7 |
