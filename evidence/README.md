# Evidence index

Raw outputs behind the claims in [FEEDBACK.md](../FEEDBACK.md) and the README results. Times are UTC
on 2026-10-10/11. Versions: runta CLI 0.2.15, runta-sdk 0.2.1 (Python), @runta/runta-sdk 0.2.0 (TS),
Harbor 0.24.0, macOS 26.6.2 arm64. No API token appears in any file (redacted at source, scanned with
`gitleaks`); local paths are shown relative to the repo.

| File | What it shows | Backs |
|---|---|---|
| `env.txt` | tool versions | context |
| `02-smoke.json` | first runtime: OS, disk, Docker, timings | the good |
| `03-checkpoint-spike.*` | what a checkpoint preserves (files, Docker, a running container), restore after the source is deleted, sibling isolation | the good |
| `04-local-repros.log` | second reproductions: Python 3.9 install, `create(image=)`, `create(runtime_agent=)`, non-TTY `--help`, `checkpoint ls --all` | U3, U4, U6, U7, bad #4 |
| `05-probe.json` | exec latency (n=20), which background patterns survive an exec, IP/CIDR allowlists | bad #5, bad #6, U8 |
| `06-egress-propagation.json` | 5 policy changes on one runtime, every probe until enforced | bad #1, U8 |
| `07-harbor-*.log`, [`../jobs/`](../jobs/) | Harbor `-e runta` oracle/nop runs; verifiers fail on the GitHub `uv` download | bad #3 |
| `09-tour.log` | secret stubs, `${credential}` template rejection, public URL formats, auto-suspend | the good, U1, U10, bad #2 |
| `10-qemu-verifier-404.log` | qemu-startup's verifier fails upstream (Debian 404, reproduced outside Runta) | not a Runta issue |
| `11-ts-sdk-list.log` | TypeScript SDK works; its create options | the good, bad #4 |
| `12b-wake-probe-fixed.log` | server reachable before idling; suspended runtime not woken by requests; manual resume 95 s | bad #2 |
| `13-image-build.log`, `16-image-runtime*.json` | Runtime Image build, and runtime-from-image readiness (n=3) | the good, bad #4, README results |
| `15-download-probe.log` | same GitHub asset via gateway, TLS passthrough, and a local control; certificate issuer on each path | bad #3 |
| `17-egress-container-method.json` | deny-all blocks host and container (gateway 403); 2 more policy changes | the good, bad #1 |
| `18-container-download.progress.log`, `18b-*` | host-level GitHub download stalls (0 bytes in 150 s) while a 20 MB non-GitHub download succeeds; local control during the stall. The second column used a bare container without Runta's CA and is not a finding. | bad #3 |
| `19-download-watch.json` | 14 paired rounds: default gateway vs TLS passthrough | bad #3 |
| `20-sdk-option-coverage.txt` | create-time options missing from both SDKs, with positive controls | bad #4 |
| `21-token-xray.json` | zero-cost Token X-Ray attempt (no capture) | not tested |
| `22-rest-errors.log` | REST error envelope and `request_id`; SDK error classes | the good, bad #7 |
| `23-pause-resume.run1.log`, `.run2.log` | pause → resume timings, state kept, 429 "no available compute capacity" | bad #2 |
| `24-docs-lint.txt`, `24b-*`, `24c-*` | docs linter output, then each hit confirmed twice with a sentinel flag | U11–U14 |
| `25-container-vs-host.*` | gateway CA and `*_CA_BUNDLE` injected into containers; host vs container downloads | the good, bad #3 |
| `26-stalled-runtime-download.log` | a stalled transfer doesn't recover while a new request succeeds | bad #3 |
| `L-*.log`, `L-*.txt` | single-finding reproductions (see IDs below) | ugly, bad #6 |
| `99-final-verify-*.log` | final re-run of every reproduction ([`../scripts/final_verify.sh`](../scripts/final_verify.sh)) | all |

## Finding IDs

Scripts and logs tag findings with `L-xx` IDs. They map to FEEDBACK.md as follows:

| ID | FEEDBACK | ID | FEEDBACK |
|---|---|---|---|
| L-01 | U5 | L-21 | bad #6 |
| L-02 | U9 | L-22 | bad #5 |
| L-03 | U7 | L-24 | U2 |
| L-11 | U8 | L-25 | U1 |
| L-14, L-30 | bad #4 | L-26 | bad #6 |
| L-16 | U6 | L-27 | U10 |
| L-18 | U3 | L-28, L-33 | bad #2 |
| L-20 | U4 | L-29 | bad #3 |
|  |  | L-31 | bad #7 |
