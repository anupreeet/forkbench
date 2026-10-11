"""Static check: do the `runta …` commands in Runta's public docs use subcommands/flags the CLI has?

For every page, extract code blocks, find lines that start with `runta `, resolve the subcommand
path against `runta help <path>` (JSON), and flag any `--long`/`-s` option the CLI doesn't declare.
Read-only: fetches public pages and runs `runta help`; creates nothing. Every hit still needs a
manual check before it's reported (e.g. a flag may be accepted but undocumented in help).

Usage: .venv/bin/python scripts/docs_lint.py > evidence/24-docs-lint.txt
"""

import functools
import html
import json
import re
import shlex
import subprocess
import time
import urllib.request

BASE = "https://runta.com"
PAGES = [
    "/docs/", "/docs/overview/", "/docs/runtime/runtime-basic/", "/docs/runtime/token-x-ray/",
    "/docs/runtime/auto-suspend-and-wake-up/", "/docs/runtime/files/", "/docs/runtime/egress/",
    "/docs/runtime/checkpoints/", "/docs/runtime/secrets-and-secret-injection/",
    "/docs/runtime/publish-a-service/", "/docs/runtime/memory-auto-scaling/",
    "/docs/organization/team-access-and-roles/", "/docs/cloud-agents/", "/docs/cloud-agents/quickstart/",
    "/docs/cloud-agents/model-providers/", "/docs/cloud-agents/lifecycle/",
    "/docs/cloud-agents/computer-sessions/", "/docs/reference/agent-skills/",
    "/docs/reference/agent-skills/runta-installer/", "/docs/reference/agent-skills/runta-demos/",
    "/docs/reference/agent-skills/runta-cli/", "/docs/integrations/harbor/",
    "/docs/reference/cli/", "/docs/reference/cli/install/", "/docs/reference/cli/runtime-commands/",
    "/docs/reference/cli/exec/", "/docs/reference/cli/files/", "/docs/reference/cli/checkpoints/",
    "/docs/reference/cli/egress/", "/docs/reference/cli/secrets/",
]
GLOBAL = {"--endpoint", "--token", "--config", "--json", "--verbose", "-v", "--help", "-h", "--version", "-V"}


@functools.cache
def help_for(path: tuple[str, ...]) -> dict | None:
    p = subprocess.run(["runta", "help", *path], capture_output=True, text=True, timeout=30)
    try:
        cmd = json.loads(p.stdout).get("command")
    except json.JSONDecodeError:
        return None
    if not cmd or (path and cmd.get("name") != path[-1]):
        return None
    return cmd


def resolve(tokens: list[str]) -> tuple[tuple[str, ...], list[str]]:
    """Walk subcommands as far as `runta help` knows them; return (path, remaining tokens)."""
    path: tuple[str, ...] = ()
    i = 0
    while i < len(tokens):
        cmd = help_for(path)
        subs = {s["name"] for s in (cmd or {}).get("subcommands", [])}
        aliases = {a: s["name"] for s in (cmd or {}).get("subcommands", []) for a in s.get("aliases", [])}
        tok = tokens[i]
        if tok in subs:
            path += (tok,)
        elif tok in aliases:
            path += (aliases[tok],)
        else:
            break
        i += 1
    return path, tokens[i:]


def flags_of(cmd: dict) -> set[str]:
    out = set(GLOBAL)
    for a in cmd.get("args", []):
        if a.get("long"):
            out.add("--" + a["long"])
        if a.get("short"):
            out.add("-" + a["short"])
    return out


def code_lines(page_html: str) -> list[str]:
    blocks = re.findall(r"<code[^>]*>(.*?)</code>", page_html, flags=re.S)
    lines: list[str] = []
    for b in blocks:
        b = re.sub(r'<div class="ec-line', '\n<div class="ec-line', b)  # one rendered line per div
        b = re.sub(r"<br\s*/?>", "\n", b)
        text = html.unescape(re.sub(r"<[^>]+>", "", b))
        joined = re.sub(r"\\\n\s*", " ", text)  # join shell line continuations
        lines += [ln.strip() for ln in joined.splitlines()]
    # CLI lines only: skip Python like `runta = Runta()` / `runta.runtimes...`
    return [ln for ln in lines if ln.startswith("runta ") and not re.match(r"runta\s*[=.(]", ln)]


def main() -> None:
    print(f"# docs lint {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}, runta "
          + json.loads(subprocess.run(["runta", "--version"], capture_output=True, text=True).stdout)["version"])
    total = issues = 0
    for page in PAGES:
        try:
            req = urllib.request.Request(BASE + page, headers={"User-Agent": "forkbench-docs-lint"})
            body = urllib.request.urlopen(req, timeout=30).read().decode()  # noqa: S310 - fixed https host
        except Exception as e:  # noqa: BLE001
            print(f"\n## {page}: fetch failed: {e}")
            continue
        for line in dict.fromkeys(code_lines(body)):
            total += 1
            try:
                tokens = shlex.split(line)[1:]
            except ValueError:
                continue
            path, rest = resolve(tokens)
            cmd = help_for(path)
            if not path or cmd is None:
                issues += 1
                print(f"\n## {page}\n  `{line}`\n  -> unknown subcommand (resolved: {' '.join(path) or '-'})")
                continue
            known = flags_of(cmd)
            if "--" in rest:  # everything after `--` is the remote command, not runta flags
                rest = rest[: rest.index("--")]
            used: list[str] = []
            for t in rest:
                if t.startswith("--") and len(t) > 2:
                    used.append(t.split("=")[0])
                elif t.startswith("-") and len(t) > 1 and not t[1:].isdigit():
                    used += [f"-{c}" for c in t[1:]]  # -it == -i -t
            unknown = [f for f in used if f not in known]
            if unknown:
                issues += 1
                print(f"\n## {page}\n  `{line}`\n  -> `runta {' '.join(path)}` has no {', '.join(unknown)}")
        time.sleep(0.5)
    print(f"\n# {total} unique `runta` command lines checked across {len(PAGES)} pages; {issues} flagged")


if __name__ == "__main__":
    main()
