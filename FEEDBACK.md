# Runta feedback: the good, the bad, the ugly

**TL;DR**
- **Built:** forkbench, which compares resetting RL task environments by rebuilding per trial vs restoring a
  Runta checkpoint. Checkpoints cut reset time by 22–61% across 4 Terminal-Bench tasks and pay for
  themselves after 1.8–8.5 trials, and 25/25 forks started clean ([results](README.md#results-2026-10-11-56-trials-114-of-runtime)).
- **Fix first:**
  1. Egress policy changes take ~20–30 s to be enforced, and the API gives no way to know when.
  2. Wake-on-request (Preview) didn't wake a suspended runtime in 3 runs. Resume takes about a minute,
     and 2 of 8 pause/resume cycles failed with "no available compute capacity".
  3. GitHub release downloads on the default egress path stall or fail, which turned 30 of 33 verifier
     runs into false-zero RL rewards. With TLS passthrough, the same download finished in ~0.2 s every
     time.
  4. Neither SDK's public `create()` can turn on Token X-Ray, Token Saving, Runtime Images, TLS
     passthrough, VNC or SSH keys.
  5. The docs' own "start a server in the background" example dies when `exec` returns, and 13 more
     copy-paste breaks between docs, CLI help and packages, including Getting Started's `--agent codex`.
- **Method:** every problem was reproduced at least twice, most of them again on 2026-10-13 (UTC) just
  before sending, and each links to raw evidence. I checked the current docs for each one: none is
  documented as intended behavior. Positives are what I observed (single runs are marked).

**Context.** I built [forkbench](README.md), a tool that resets RL task environments either from scratch
or from a Runta checkpoint, and grades every fork. It runs Terminal-Bench tasks.
- Used: CLI 0.2.15, Python SDK 0.2.1 (async), TypeScript SDK 0.2.0, REST, the docs (read 2026-10-10/11,
  re-checked 2026-10-12), Harbor 0.24.0 with `-e runta`.
- Setup: macOS arm64, free-trial org, one client location. Times are UTC.

**How to read this.** Every problem below (bad + ugly) was reproduced at least twice, or checked
against source code plus two observations. Each one links to raw evidence in [`evidence/`](evidence/)
(index and finding IDs in [`evidence/README.md`](evidence/README.md)). Positives are reported as observed.
Things I couldn't attribute are left out.

## The good

- **Checkpoints do what an RL harness needs.** A checkpoint preserved the filesystem **and running
  processes**: a Docker task container was still running after restore, with no re-pull. Restores work
  after the source runtime is deleted, and sibling forks are isolated.
  ([spike](evidence/03-checkpoint-spike.summary.md), [benchmark](README.md#results-2026-10-11-56-trials-114-of-runtime))
- **Secret stubs keep the promise** (2 runs). The upstream received the injected credential, a path
  outside the rule did not, and the value was in neither the runtime's environment nor any file under
  common directories. ([09](evidence/09-tour.log), [09b](evidence/09b-secret-stub-20261013.log))
- **Good starting machine.** Ubuntu 24.04, Docker 29.9 preinstalled, 32 GB disk, root, systemd. Harbor-style
  Docker tasks work without setup. ([02](evidence/02-smoke.json))
- **Agent-friendly CLI design.** JSON output whenever stdout isn't a TTY, and structured errors with a
  machine-readable `required_action`. ([L-20](evidence/L-20-checkpoint-ls.log))
- **Clean REST errors.** The 400, 401, 404 and 422 responses I checked all had
  `{"error":{"code","message"},"request_id"}`, with the id matching `X-Request-Id`.
  ([22](evidence/22-rest-errors.log), [22b](evidence/22b-rest-errors-20261013.log))
- **The TLS-intercepting gateway is mostly invisible to containers.** Every container I checked got
  Runta's CA file and the usual `*_CA_BUNDLE` / `SSL_CERT_FILE` variables with no mounts or setup, and
  HTTPS verified in the Debian-based task image, `python:3.12-slim` (also as a non-root user) and
  `alpine:3.20`. One exception: in `curlimages/curl`, HTTPS failed verification (curl exit 60) on every
  runtime I tried, as root or not, even though the same files and variables were there and readable.
  I didn't find out why. ([25](evidence/25-container-vs-host.json), [25c](evidence/25c-ca-and-background-20261013.json),
  [25d](evidence/25d-ca-nonroot-20261013.json), [18](evidence/18-container-download.progress.log))
- **Egress enforcement is real, including inside Docker.** Under deny-all, plain HTTP from a task
  container got `403 Forbidden` from the gateway, and host HTTPS failed at TLS. Allowlists switch
  cleanly, and IPs and CIDRs work too (see the timing caveat below).
  ([17](evidence/17-egress-container-method.json), [06](evidence/06-egress-propagation.json), [06b](evidence/06b-egress-timing-20261013.json))
- **Runtime Images from a Dockerfile just work (CLI).** `FROM <task image>` built in 224 s (1 build). The
  runtime boots straight into the task filesystem with its WORKDIR (`/app`), and the CLI explains how
  ENTRYPOINT/CMD behave. ([13](evidence/13-image-build.log), [16](evidence/16-image-runtime.json))
- **Both SDKs install and work** (Python on 3.10+, and TypeScript). The Python SDK retries create (with
  an idempotency key), patch, file transfers and delete on transient errors (408/429/502/503/504 and
  timeouts; `_internal/retry.py`). Exec and resume aren't retried, so callers still handle those.

## The bad

**1. Egress policy changes take ~20–30 s to be enforced, and nothing tells you when.**
- What happened: after `set_policy()` returned, traffic kept following the **old** policy.
  - On 2026-10-10/11, across 15 changes on 10 runtimes, the first probe that saw the new policy came
    17–35 s after `set_policy()` returned (median 25 s; probes every 3–8 s).
  - Re-run on 2026-10-13: in 6 of 6 changes, the first probe after `set_policy()` returned still saw the
    old policy, and the new one was observed 22–39 s after it returned (coarser probing that day).
  - `egress.get()` returned the new policy straight away, and the API has no applied/enforced field
    to wait on.
- The docs suggest it's immediate. After setting an allowlist, Governance and Audit says: "Try a host
  that is not on the allowlist; it should be blocked:".
- Why it matters: "lock the network, then hand the runtime to the agent" leaves roughly 25 s of open
  internet. A policy passed in the restore call is fine: deny-all was enforced at the first probe in
  4/4 tasks.
- Suggestions:
  - Make `set_policy` return only once the policy is enforced, or return a revision and expose an
    `applied_revision`/`enforced` field to wait on.
  - Document the delay in the Egress guide.
- Evidence: [06](evidence/06-egress-propagation.json), [17](evidence/17-egress-container-method.json),
  [06b](evidence/06b-egress-timing-20261013.json), benchmark egress trials ([summary](results/bench/all/summary.md))

**2. Wake-on-request (Preview) didn't wake suspended runtimes; resume takes about a minute and can fail
for lack of capacity.**
- What happened: in 3 runs, a server that answered 200 on its published URL
  (`https://8080-<id>.runta.dev`) before idling got **404** once the runtime was suspended.
  - The runtime's status stayed `suspended` through every request: 16 requests over 3 minutes in one
    run, and 6 requests in each of the other two.
  - The docs say: "Send another request to wake the runtime. The wake-up path restores the runtime and
    then proxies the original request".
  - The no-port URL form the docs use returns 404 even while the runtime is awake (U9).
- Explicit `pause()` → `resume()` took 32–68 s to the first successful command (median 54 s, n=6 across
  3 runs). Memory state survived every time: a file and a running process were still there.
- 2 of 8 pause/resume cycles, in 2 of 3 runs, failed: `resume()` returned `HTTP 429: rate_limited: the
  assigned worker has no available compute capacity`.
  - In one run, a retry 30 s later failed the same way.
  - The API reference describes 429 on resume as "Rate limit exceeded", so a client is likely to treat
    it as its own rate limit. 503 ("Upstream service is unavailable") fits the message better.
- Why it matters: idle agents are the cost case for suspend. If a request doesn't wake the runtime,
  users have to keep it running or resume it by hand, which takes about a minute.
- Evidence: [12b](evidence/12b-wake-probe-fixed.log), [99 (10-11)](evidence/99-final-verify-20261011T025911Z.log),
  [99 (10-13)](evidence/99-final-verify-20261013T000527Z.log), [23 run 1](evidence/23-pause-resume.run1.log),
  [23 run 2](evidence/23-pause-resume.run2.log), [23 run 3](evidence/23-pause-resume.run3-20261013.log)

**3. GitHub release downloads on the default egress path stall or fail, which turns into false "0"
rewards for RL verifiers.**
- What happened: Terminal-Bench test scripts `curl` uv from a GitHub release before running the tests.
  - In my 2.5-hour benchmark (2026-10-11), **30 of 33 verifier runs** on the three gradable tasks never
    ran their tests because that download failed (curl exit 56 or 18), so a correct solution scored 0.
  - All 4 Harbor `-e runta` verifier runs that reached the download failed the same way.
- Paired test: the same 17.8 MB release asset, downloaded at the same moment on runtimes that differ
  only in `--tls-passthrough-host` for `github.com` and `*.githubusercontent.com`:
  - 2026-10-11, one runtime per arm, 14 rounds. Default path: 13/14 completed (6 in under 1 s, 7 in
    7.6–11 s) and 1 got 0 bytes until the 120 s cap. Passthrough: 14/14, median 0.19 s.
  - 2026-10-13, two runtimes per arm, 10 rounds. Default path: 1 of 20 downloads completed; 15 got 0
    bytes in 120 s, and in 4 the exec connection dropped while waiting. Passthrough: 20/20, median
    0.19 s. A local control outside Runta completed 10/10.
  - The default-path runtimes weren't simply offline. In the same window they downloaded 20 MB from
    Cloudflare and 47 MB from PyPI (7.5–10.5 s) and loaded github.com, while `api.github.com` got
    0 bytes in 30 s on both. Of two extra release downloads in that check, one completed (10.3 s) and
    one stalled ([19c](evidence/19c-gateway-controls-20261013.log)).
- Stalled transfers don't recover, but a new request may work: on one runtime a fresh download succeeded
  (0.22 s) while its verifier's earlier download stayed stalled ([26](evidence/26-stalled-runtime-download.log)).
- I make no claim about the root cause.
- Workaround: `--tls-passthrough-host`, which is CLI-only (see #4) and documented only in `runta help run`.
- Evidence: [19](evidence/19-download-watch.json), [19b](evidence/19b-download-ab-20261013.json),
  [19c](evidence/19c-gateway-controls-20261013.log), [25b](evidence/25b-container-vs-host-20261013.json), [Harbor jobs](jobs/), benchmark verifier tails
  ([all.jsonl](results/bench/all.jsonl))

**4. Neither SDK's public `create()` can turn on several features the API and CLI support.**
- What happened: `runta run` has these flags, and the public `runtimes.create()` in runta-sdk 0.2.1
  (Python) and @runta/runta-sdk 0.2.0 (TS) has none of them:
  - `--llm-tool-io-capture` (Token X-Ray)
  - `--llm-token-saving-*` (Token Saving)
  - `--image` (Runtime Images)
  - `--tls-passthrough-host`
  - `--vnc`
  - `--ssh-key`
  - `--llm-session-content-capture`
- Python's `create()` takes no `**kwargs`, and `create(image=…)` raises `TypeError`.
- The REST request already has most of these. The Python package's own generated model
  (`FreshRuntimeCreateRequest`) includes `image`, `vnc_enabled`, `ssh_key_ids`,
  `llm_tool_io_capture_enabled` and `llm_token_saving_policy`, but the public method doesn't pass them
  through.
- Why it matters:
  - A team building on the SDK has to drop to the CLI or raw REST to use these on the runtimes it
    creates. (Token X-Ray's docs only cover the dashboard and CLI; Token Saving is marked Preview.)
  - For RL resets, a prebuilt image is a real alternative to checkpoints: a median of 71.8 s to a ready
    environment (n=3). I had to shell out to the CLI to get it ([probe](scripts/probe_image_runtime.py)).
- Evidence: [20](evidence/20-sdk-option-coverage.txt) (method + positive controls),
  [04](evidence/04-local-repros.log), [99 (10-13)](evidence/99-final-verify-20261013T000527Z.log)

**5. Every `exec` costs about 1 s, even after the connection is open.**
- What happened: the Python SDK opens a new WebSocket for every exec.
  - On 2026-10-11, a no-op exec took a median of 1.05–1.07 s end to end (n=20 in each of two sessions),
    and 1.05 s across the benchmark's 48 trials.
  - On 2026-10-13 I timed the parts separately. Once the WebSocket was open, sending `true` and getting
    its exit back still took a median of 1.04 s (n=20), while a TCP connect to the same API host took
    about 0.2 s. Opening the WebSocket took another 2.0 s that day, on a slower client network.
- Why it matters: agents and verifiers issue many small commands. At this rate, 100 tool calls spend
  about 100 s waiting, on top of network time.
- Suggestions: find where the ~1 s inside exec goes, and let one connection carry many commands.
- Measured from one client location.
- Evidence: [05](evidence/05-probe.json), [05b](evidence/05b-exec-latency-20261013.json),
  [99 (10-11)](evidence/99-final-verify-20261011T025911Z.log)

**6. Background processes started in an `exec` are killed when it returns, including the docs' own
example.**
- What happened: the docs start a server with
  `runta exec demo -- sh -lc 'nohup python3 -m http.server 8080 >/tmp/http.log 2>&1 &'`
  (Auto-suspend page, runta-demos skill).
  - Run exactly like that, the server was gone 5 s later and port 8080 didn't answer, in 2 of 2 runs.
  - The same server started with `setsid -f` answered 200.
  - Through the Python SDK, `nohup … &` died the same way in 4 sessions (plain `cmd &` in the 1 session
    that tried it), while `setsid -f …` survived every time.
- Why it matters: "start a dev server in the background" is a standard agent move, and the documented
  way doesn't work.
- Suggestions: change the docs to `setsid -f`, and/or add a background option to exec.
- Evidence: [05c](evidence/05c-docs-background-example-20261013.log),
  [25c](evidence/25c-ca-and-background-20261013.json), [05](evidence/05-probe.json),
  [99 (10-11)](evidence/99-final-verify-20261011T025911Z.log), [99 (10-13)](evidence/99-final-verify-20261013T000527Z.log)

**7. SDK errors drop the `request_id` the API returns.**
- What happened: every error response I checked (400, 401, 404, 422) carried a `request_id` matching
  `X-Request-Id`. The SDK's own generated `ErrorEnvelope` describes it as a "UUID v7 correlation ID
  shared with the `X-Request-Id` response header".
  - Python's `ApiError` keeps only `status_code` and `code`.
  - TS's keeps only `statusCode` and `code`.
- The docs ask for it: "Keep request_id when investigating a failure." (Cloud Agents API reference).
- Suggestion: add `request_id` to `ApiError` in both SDKs.
- Evidence: [22](evidence/22-rest-errors.log), [22b](evidence/22b-rest-errors-20261013.log)

## The ugly (docs, help and packages vs reality)

Each item gives what the docs, help or package say, then what CLI 0.2.15 / SDK 0.2.1 actually do. All
were reproduced at least twice; the last run was 2026-10-12/13. U10–U13 were found by
[`scripts/docs_lint.py`](scripts/docs_lint.py), which checks all 216 `runta …` lines in 30 docs pages
against `runta help`. It flagged 17 lines: 3 were linter false positives (bare `runta --help`), and
each of the other 14 was confirmed by invoking the CLI with a sentinel flag, so nothing executes.

| # | Where | Docs / help say | Actually |
|---|---|---|---|
| U1 | Secret stubs guide, CLI secrets reference, demos skill | `--template 'Bearer ${credential}'` ("Use ${credential} for the stored value") | Rejected: `--template must contain ${secret} exactly once` ([09](evidence/09-tour.log), [99](evidence/99-final-verify-20261013T000527Z.log)) |
| U2 | JSON help (`runta help <cmd>` when stdout isn't a TTY), which CLI errors tell agents to read | `--wait`, `--vnc`, `--llm-tool-io-capture`, `--value-stdin`, `--prompt`: `"possible_values":["true","false"]` | `--wait true`, `--vnc true` and `--llm-tool-io-capture true` all give `unexpected argument 'true'`; these are bare flags ([L-24](evidence/L-24-bool-flags.log), [L-24b](evidence/L-24b-bool-flags-20261013.log)) |
| U3 | `runta <cmd> --help` when stdout isn't a TTY (scripts, CI, agents) | the subcommand's help | the **root** help (`command.name: "runta"`); `runta help <cmd>` works ([L-18](evidence/L-18-subcommand-help.log), [99](evidence/99-final-verify-20261013T000527Z.log)) |
| U4 | CLI checkpoints reference | `runta checkpoint ls --all` ("Include system-managed idle checkpoints") | `unexpected argument '--all'`; `runta help checkpoint ls` lists no flags ([L-20](evidence/L-20-checkpoint-ls.log), [99](evidence/99-final-verify-20261013T000527Z.log)) |
| U5 | OpenAI Agents integration | `pip install openai-agents-runta` | no such package on public PyPI ([L-01](evidence/L-01-openai-agents-runta.log), [99](evidence/99-final-verify-20261013T000527Z.log)); Runta's PyPI README uses `pip install "runta-sdk[openai]"` for the same provider |
| U6 | Python SDK reference | `create(…, runtime_agent=…)` | `TypeError` in Python 0.2.1; TS 0.2.0 raises "runtimeAgent is no longer supported by the v2 API". The reference looks generated from an older SDK (`llms.txt` says "SDK version: 0.1.4") ([04](evidence/04-local-repros.log), [99](evidence/99-final-verify-20261013T000527Z.log)) |
| U7 | Getting Started | `pip install runta-sdk` | needs Python ≥ 3.10, which Getting Started doesn't mention (PyPI and the OpenAI Agents page do). With macOS's `/usr/bin/python3` (3.9.6), pip says only `from versions: none` ([L-03](evidence/L-03-py39-pip-install.log), [99](evidence/99-final-verify-20261013T000527Z.log)) |
| U8 | npm `@runta/runta-sdk` repository/homepage; Homebrew formula homepage | `github.com/runta-dev/typescript-sdk`, `github.com/runta-dev/runta` | both 404 for the public, so there's no public place to read the SDK/CLI source or file an issue ([99 (10-11)](evidence/99-final-verify-20261011T025911Z.log), [L-02](evidence/L-02-package-links-20261013.log)) |
| U9 | Auto-suspend page; runta-demos skill ("Do not add `:8080` to the public URL.") | `https://<runtime_id>.runta.dev` | 404 even while awake; `https://8080-<runtime_id>.runta.dev` (the Publish page and `runta ports ls`) works ([09](evidence/09-tour.log), [99](evidence/99-final-verify-20261013T000527Z.log)) |
| U10 | **Getting Started** (`/docs/`): "Create a Codex agent runtime" | `runta run --name codex-demo … --agent codex` | `unexpected argument '--agent'`. Also `--agent pi --no-shell` in the runta-demos / runta-cli skills ([24b](evidence/24b-docs-lint-verify.log), [24c](evidence/24c-docs-lint-verify-run2.log)) |
| U11 | runta-cli agent skill | `runta agents ls` | `unrecognized subcommand 'agents'` ([24b](evidence/24b-docs-lint-verify.log), [24c](evidence/24c-docs-lint-verify-run2.log)) |
| U12 | Secrets guide + skills | `runta secret rule list --runtime demo` | `--runtime` unknown; the runtime is positional (`… rule ls demo`) ([24b](evidence/24b-docs-lint-verify.log), [24c](evidence/24c-docs-lint-verify-run2.log)) |
| U13 | Secrets guide, CLI secrets + egress reference | `runta secret rule set -f rules.yaml`, `runta egress set -f <file>` | `-f` unknown on both ([24b](evidence/24b-docs-lint-verify.log), [24c](evidence/24c-docs-lint-verify-run2.log)) |

## What I'd do first

1. **Make egress changes observable.** Return from `set_policy` only once the policy is enforced, or
   expose an enforced revision to wait on. Document the delay meanwhile. This is a security
   expectation, not a nice-to-have.
2. **Test the docs in CI.** Most of "the ugly" and bad #6 are drift between docs, CLI and SDK.
   - A static check like [`scripts/docs_lint.py`](scripts/docs_lint.py) (every `runta …` line in the docs
     vs `runta help`) catches U10–U13 today.
   - Executing snippets in CI would also catch U1, U5, U9 and the background-server example.
   - Generating the SDK reference pages from the SDK itself would have caught U6.
3. **Expose the API's create options in both SDKs' public `create()`.** The Python package already
   carries the generated fields, so Token X-Ray, Token Saving, images, VNC and SSH keys are mostly
   plumbing. Add TLS passthrough to the API.
4. **Treat agents as first-class CLI users.** Fix `--help` without a TTY (U3) and the on/off flag
   metadata in JSON help (U2). CLI errors send agents to the JSON help through `required_action`, so
   it has to be right.
5. **For RL workloads specifically:** a batch "restore N copies of checkpoint X" call plus the
   Harbor `reset=checkpoint` option ([README](README.md#next-step-make-it-a-one-flag-option-in-harbor))
   would turn FrontierHarness's golden-checkpoint step into a product feature.

## What I didn't test (so absence here means "not tested", not "fine")
- **Cloud Agents and Token X-Ray end to end:** both need paid model access. A zero-cost attempt (one
  request rejected upstream with 401) produced no Token X-Ray capture, which may be by design.
  ([21](evidence/21-token-xray.json))
- The dashboard UI, team roles, SSH, VNC, GitHub repository checkout, memory auto-scaling.
- Scale: I ran at most a handful of runtimes at once on a free-trial org, from one client location,
  over two evenings (2026-10-10/11 and 2026-10-12 PT).
