# Runta feedback: the good, the bad, the ugly

**TL;DR**
- **Built:** forkbench, which compares resetting RL task environments by rebuilding per trial vs restoring a
  Runta checkpoint. Checkpoints cut reset time by 22–61% across 4 Terminal-Bench tasks and pay for
  themselves after 1.8–8.5 trials, and 25/25 forks started clean ([results](README.md#results-2026-10-11-56-trials-114-of-runtime)).
- **Fix first:**
  1. Egress policy changes take 17–35 s to apply, while the API reports them done.
  2. Wake-on-request (Preview) never woke a suspended runtime (3/3 runs). Resume takes ~1 min, and in
     2/5 cycles it failed with "no available compute capacity".
  3. GitHub release downloads through the default egress path are slow or stall, which shows up as
     false-zero RL rewards (paired A/B against TLS passthrough).
  4. Token X-Ray, Token Saving, images and TLS passthrough are unreachable from both SDKs.
  5. 14 docs/CLI mismatches, including the Getting Started agent example (`--agent codex`).
- **Method:** every problem reproduced at least twice, with raw evidence linked. Positives are what I
  observed (single runs are marked). Untested areas are listed at the end.

**Context.** I built [forkbench](README.md), a tool that resets RL task environments either from scratch
or from a Runta checkpoint, and grades every fork. It runs Terminal-Bench tasks.
- Used: CLI 0.2.15, Python SDK 0.2.1 (async), TypeScript SDK 0.2.0, REST, the docs (read 2026-10-10/11), Harbor 0.24.0 with `-e runta`.
- Setup: macOS arm64, free-trial org.

**How to read this.** Every problem below (bad + ugly) was reproduced at least twice, or checked
against source code plus two independent observations. Positives are reported as observed. Each one links to raw evidence in [`evidence/`](evidence/).
Documented behavior is never called a bug. Things I couldn't attribute are left out.

## The good

- **Checkpoints do what an RL harness needs.** A checkpoint preserved the filesystem **and running
  processes**: a Docker task container was still running after restore, with no re-pull. Restores work
  after the source runtime is deleted, and sibling forks are isolated.
  ([spike](evidence/03-checkpoint-spike.summary.md), benchmark)
- **Secret stubs keep the promise** (1 run). The upstream received the injected credential. A path
  outside the rule did not get it. The value was nowhere in the runtime's environment or common
  directories. ([tour](evidence/09-tour.log))
- **Good starting machine.** Ubuntu 24.04, Docker 29.9 preinstalled, 32 GB disk, root, systemd. Harbor-style
  Docker tasks work without setup.
- **Agent-friendly CLI design.** JSON output whenever stdout isn't a TTY. Structured errors with a
  machine-readable `required_action`. Scoped API keys with a default 30-day expiry.
- **Clean REST errors.** The 401 and 404 I checked had `{"error":{"code","message"},"request_id"}`,
  with the id matching `X-Request-Id` ([22](evidence/22-rest-errors.log)).
- **TLS interception is mostly invisible to containers.** Docker containers on a runtime automatically
  trust Runta's gateway: its CA file and the usual `*_CA_BUNDLE` env vars are injected with no mounts, so
  Harbor-style task containers just work. ([25](evidence/25-container-vs-host.json))
- **Egress enforcement is real, including inside Docker.** Under deny-all, plain HTTP from a task
  container got `403 Forbidden` from the gateway, and host HTTPS failed at TLS. Allowlists switch
  cleanly, and IPs and CIDRs work too (see the timing caveat below).
  ([17](evidence/17-egress-container-method.json))
- **Runtime Images from a Dockerfile just work (CLI).** `FROM <task image>` built in 224 s (1 build). The
  runtime boots straight into the task filesystem with its WORKDIR (`/app`), and the CLI explains how
  ENTRYPOINT/CMD behave. ([13](evidence/13-image-build.log), [16](evidence/16-image-runtime.json))
- **Both SDKs install and work.** Python on 3.10+; TS on Node 26. The Python SDK has bounded retries
  for replay-safe operations on 408/429/502/503/504 and transport timeouts (`_internal/retry.py`), so
  callers don't need their own retry layer.

## The bad

**1. Egress policy changes take ~17–35 s to apply, but the API says they're done immediately.**
- What happened: across 16 changes on 11 runtimes, `set_policy()` returned in about 1.7 s and
  `egress.get()` showed the new policy at once. Traffic kept following the **old** policy for
  17–35 s (median 25 s), then switched at once, for the host and Docker containers together.
- Why it matters: the common pattern "lock the network, then hand the runtime to the agent" leaves
  roughly 25 s of open internet. A policy passed in the restore call is fine: deny-all was enforced at
  the first probe in 4/4 tasks.

- Suggestions:
  - Make `set_policy` return only once the policy is enforced, or return a revision and expose an
    `applied_revision`/`enforced` field to wait on.
  - Document the delay in the Egress guide.
- Evidence: [06](evidence/06-egress-propagation.json), [17](evidence/17-egress-container-method.json), benchmark egress trials ([summary](results/bench/all/summary.md))

**2. Wake-on-request (Preview) didn't wake suspended runtimes; resume is slow and can fail on capacity.**
- What happened: in 3/3 runs, a server reachable at its published URL before idling returned **404**
  to every request once the runtime was `suspended`. Up to 16 requests over 3 minutes never woke it.
  The docs say "Send another request to wake the runtime … restores the runtime and then proxies the
  original request".
- In one run, manual `runta resume` worked but took about 95 s to reach `running`. Explicit `pause()` → `resume()`
  took 48–68 s to the first successful command (median 54.5 s, n=3). Memory state survived every time:
  the file and a running process were still there.
- In 2 of 5 pause/resume cycles (2 separate runs), the runtime could not be resumed. `resume()` failed
  within ~1.5 s with `HTTP 429: rate_limited: the assigned worker has no available compute capacity`,
  and in run 2 a retry 30 s later failed the same way. My org was far below its quota, and the code says
  "rate limited" while the message says "capacity".
- Why it matters: idle agents are the cost case for suspend. For context, a third-party benchmark with
  the same "ready = a command ran" definition measured resumes of 0.66–3.3 s on five other sandbox
  platforms ([LogRocket, Aug 2026](https://blog.logrocket.com/comparing-ai-agent-sandbox-platforms-e2b-modal-daytona-and-more/)).
  Different setup and time, so it's context, not a head-to-head.
- Evidence: [09](evidence/09-tour.log), [12b](evidence/12b-wake-probe-fixed.log),
  [99](evidence/99-final-verify-20261011T025911Z.log), [23 run 1](evidence/23-pause-resume.run1.log), [23 run 2](evidence/23-pause-resume.run2.log)

**3. GitHub release downloads through the default egress path are often slow and sometimes stall,
which turns into false "0" rewards for RL verifiers.**
- What happened: I ran 14 paired rounds, downloading the same 17.8 MB GitHub release asset at the same
  time on two runtimes. The only difference between them was `--tls-passthrough-host` for GitHub hosts.
  - Default (intercepting) path: median 7.6 s, 13/14 OK, one stalled at the 120 s cap.
  - Passthrough path: median 0.19 s, 14/14 OK.
- Earlier the same evening, stalls were frequent (0 bytes in 120–150 s). All 4 Harbor `-e runta`
  verifier runs that reached this download failed on it.
- Stalled transfers don't recover, but the next request may work. One minute apart, a fresh download
  on one runtime got 0 bytes in 60 s while another runtime's took 8.9 s, and on that second runtime a
  new request succeeded (0.22 s) while its verifier's earlier download stayed stalled
  ([26](evidence/26-stalled-runtime-download.log)).
- A 20 MB non-GitHub download was fast through the gateway (n=2), so I'm not generalizing beyond GitHub
  release assets, and I make no claim about root cause.
- Why it matters: Terminal-Bench test scripts `curl` uv from GitHub releases. A stall becomes a reward of
  0 for a correct solution, which is noise injected straight into an RL signal.
  - In my 2.5-hour benchmark, **30 of 33 verifier runs** on the gradable tasks failed on this download.
  - Twice, the reference solution's own `git clone` from GitHub failed the same way.
- Workaround: `--tls-passthrough-host`, which is CLI-only (see #4).
- Evidence: [19](evidence/19-download-watch.json), [Harbor jobs](jobs/)

**4. The SDKs can't turn on several headline features at runtime creation.**
- What happened: these `runta run` flags appear **zero times** in both runta-sdk 0.2.1 (Python) and
  @runta/runta-sdk 0.2.0 (TS), and Python's `create()` has no `**kwargs`:
  - `--llm-tool-io-capture` (Token X-Ray)
  - `--llm-token-saving-*` (Token Compression)
  - `--image` (Runtime Images)
  - `--tls-passthrough-host`
  - `--vnc`
  - `--ssh-key`
  - `--llm-session-content-capture`
- Example: `create(image=…)` raises `TypeError`.
- Why it matters:
  - A team building on the SDK can't use Token X-Ray, a headline feature in Runta's docs, for the
    runtimes it creates.
  - For RL resets, a prebuilt image is a real alternative to checkpoints. I measured a median of 71.8 s to a
    ready environment (n=3), but had to shell out to the CLI to get it ([probe](scripts/probe_image_runtime.py)).
- Evidence: [20](evidence/20-sdk-option-coverage.txt) (method + positive controls), [04](evidence/04-local-repros.log)

**5. Every `exec` costs about 1 s of round trip.**
- What happened: a no-op command (`true`) took a median of 1.05–1.07 s across 3 sessions (n=20, 5, 20;
  min 0.86 s) via the Python SDK. A plain `GET /healthz` from the same machine took 0.14–0.24 s.
- The SDK sends exec as a single HTTP request with no client-side polling (checked in the source), so the
  extra time is on the exec path itself.
- Why it matters: agents and verifiers often issue many small commands. At this rate, 100 tool calls
  spend about 100 s on round trips alone.
- Measured from one client location.
- Evidence: [05](evidence/05-probe.json)

**6. Background processes started in an `exec` are killed when it returns, and the docs don't say so.**
- What happened: `cmd &`, `nohup … &`, `nohup … & disown` and `setsid … &` were all dead seconds later.
  Only `setsid -f …` and `systemd-run …` survived. Reproduced in 2 sessions.
- Why it matters: "start a dev server in the background" is a standard agent move.
- Also, `exec_detached()` sounds like a server-side detach but is a local thread/asyncio task (SDK source).
- Suggestion: document it, and/or add a `background=True` exec option, which
  [E2B documents](https://docs.e2b.dev/commands/background) for this case.
- Evidence: [05](evidence/05-probe.json), [L-26](evidence/L-26-exec-detached-source.txt)

**7. SDK errors drop the `request_id` the API returns.**
- What happened: the API returns `request_id` (matching `X-Request-Id`) on every error. Python's
  `ApiError` keeps only `status_code` and `code`, and TS's only `statusCode` and `code`.
- Why it matters: the one id support needs is thrown away exactly when a user hits an error.
- Evidence: [22](evidence/22-rest-errors.log)

## The ugly (copy-paste breaks)

Each item: what the docs/help say → what CLI 0.2.15 / SDK 0.2.1 actually do. All reproduced at least twice.
U11–U14 were found by [`scripts/docs_lint.py`](scripts/docs_lint.py), which checks all 216 `runta …` lines in 30 docs pages against
`runta help`. Each hit was then confirmed by invoking the CLI with a sentinel flag, so nothing executes.

| # | Where | Docs / help say | Actually |
|---|---|---|---|
| U1 | Secret stubs guide + demos skill | `--template 'Bearer ${credential}'` | Rejected: `--template must contain ${secret} exactly once` ([09](evidence/09-tour.log)) |
| U2 | `runta help <cmd> --json` (what CLI errors tell agents to read) | `--wait`, `--vnc`, `--value-stdin`, `--prompt`, `--llm-tool-io-capture`: `"possible_values":["true","false"]` | `--wait true` gives `unexpected argument 'true'`; they're bare flags ([L-24](evidence/L-24-bool-flags.log)) |
| U3 | `runta <cmd> --help` when stdout isn't a TTY (scripts, CI, agents) | the subcommand's help | the **root** help (`command.name: "runta"`, 5 global flags); `runta help <cmd>` works ([L-18](evidence/L-18-subcommand-help.log)) |
| U4 | CLI checkpoints reference | `runta checkpoint ls --all` | `unexpected argument '--all'`; `runta help checkpoint ls` lists no flags ([L-20](evidence/L-20-checkpoint-ls.log)) |
| U5 | OpenAI Agents integration | `pip install openai-agents-runta` | no such package on public PyPI ([L-01](evidence/L-01-openai-agents-runta.log)) |
| U6 | Python SDK reference | `create(…, runtime_agent=…)` | `TypeError` in 0.2.1 (the TS SDK has `runtimeAgent`) ([04](evidence/04-local-repros.log)) |
| U7 | Getting started | `pip install runta-sdk` | needs Python ≥ 3.10 (not stated). With macOS's `/usr/bin/python3` (3.9.6 here), pip says only `from versions: none` ([L-03](evidence/L-03-py39-pip-install.log)) |
| U8 | Egress guide; Harbor integration page | "hostnames or wildcard host patterns"; "IP addresses and CIDR ranges aren't supported" | IPs and CIDRs are accepted (SDK + CLI) and enforced correctly, matching the API reference and `runta help egress set` ("hosts, IP addresses, or CIDR ranges") ([06](evidence/06-egress-propagation.json)) |
| U9 | npm `@runta/runta-sdk` repository/homepage; Homebrew formula homepage | `github.com/runta-dev/typescript-sdk`, `github.com/runta-dev/runta` | 404 for the public: no place to read the source or file an issue |
| U10 | Auto-suspend page; runta-demos agent skill ("omits the port … do not add `:8080`") | `https://<runtime_id>.runta.dev` | 404 even while awake; `https://8080-<runtime_id>.runta.dev` (the Publish page and `runta ports ls`) works ([09](evidence/09-tour.log), [99](evidence/99-final-verify-20261011T025911Z.log)) |
| U11 | **Getting Started** (`/docs/`): "Create a Codex agent runtime" | `runta run --name codex-demo … --agent codex` | `unexpected argument '--agent'`. Also `--agent pi --no-shell` in the runta-demos / runta-cli skills ([24b](evidence/24b-docs-lint-verify.log), [24c](evidence/24c-docs-lint-verify-run2.log)) |
| U12 | runta-cli agent skill | `runta agents ls` | `unrecognized subcommand 'agents'` |
| U13 | Secrets guide + skills | `runta secret rule list --runtime demo` | `--runtime` unknown; the runtime is positional (`… rule ls demo`) |
| U14 | Secrets guide, CLI secrets + egress reference | `runta secret rule set -f rules.yaml`, `runta egress set -f <file>` | `-f` unknown on both |

## What I'd do first

1. **Make egress changes observable.** Return from `set_policy` only once the policy is enforced, or
   expose an enforced revision to wait on. Document the delay meanwhile. This is a security
   expectation, not a nice-to-have.
2. **Test the docs in CI.** Most items in "the ugly" are drift between docs, CLI and SDK.
   - A static check like [`scripts/docs_lint.py`](scripts/docs_lint.py) (every `runta …` line in the docs
     vs `runta help`) catches U4 and U11–U14 today.
   - Executing snippets in CI would also catch U1, U5 and U10.
   - Generating the SDK reference pages from the SDK itself would have caught U6.
3. **Generate both SDKs' `create()` from the same API spec as the CLI**, so Token X-Ray, Token Saving,
   images, TLS passthrough, VNC and SSH keys reach SDK users. Docs, SDKs and CLI would then share one
   source of truth (fixes U6 too).
4. **Treat agents as first-class CLI users.** Fix `--help` without a TTY (U3) and boolean metadata in
   JSON help (U2). Agents follow `required_action: runta help --json` literally.
5. **For RL workloads specifically:** a batch "restore N copies of checkpoint X" call plus the
   Harbor `reset=checkpoint` option ([README](README.md#next-step-make-it-a-one-flag-option-in-harbor))
   would turn the FrontierHarness bash workaround into a product feature.

## What I didn't test (so absence here means "not tested", not "fine")
- **Cloud Agents and Token X-Ray end to end:** both need paid model access. A zero-cost attempt (one
  request rejected upstream with 401) produced no Token X-Ray capture, which may be by design.
  ([21](evidence/21-token-xray.json))
- The dashboard UI, team roles, SSH, VNC, GitHub repository checkout, memory auto-scaling.
- Scale: I ran at most a handful of runtimes at once on a free-trial org (32 vCPU cap), from one client
  location, over one evening (2026-10-10/11 PT).
