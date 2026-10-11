"""forkbench CLI: doctor | run | report | cleanup."""

from __future__ import annotations

import asyncio
import platform
import secrets
import sys
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Annotated, Any

import typer

from forkbench.log import setup_logging
from forkbench.report import write_reports
from forkbench.runner import BudgetExceeded, RunConfig, Runner
from forkbench.sandbox import purge
from forkbench.settings import RESOURCE_PREFIX, ConfigError, Settings, load_settings
from forkbench.taskspec import TaskError, load_task

app = typer.Typer(add_completion=False, no_args_is_help=True, help=__doc__)


def _settings(**overrides: Any) -> Settings:
    try:
        return load_settings(**overrides)
    except ConfigError as exc:
        typer.secho(f"config error: {exc}", fg="red", err=True)
        raise typer.Exit(2) from exc


def _client(s: Settings) -> Any:
    from runta import AsyncRunta

    return AsyncRunta(token=s.token.get_secret_value(), endpoint=s.endpoint)


@app.command()
def doctor() -> None:
    """Check Python, SDK, token and API access. Never prints the token."""
    ok = sys.version_info >= (3, 10)
    typer.echo(
        f"python      {platform.python_version()} {'ok' if ok else '(runta-sdk needs >=3.10)'}"
    )
    typer.echo(f"runta-sdk   {version('runta-sdk')}")
    s = _settings()
    typer.echo(f"token       present ({len(s.token.get_secret_value())} chars, not shown)")
    typer.echo(f"endpoint    {s.endpoint or 'SDK default'}")

    async def probe() -> None:
        async with _client(s) as client:
            runtimes = await client.runtimes.list()
            mine = [r for r in runtimes if str(r.name).startswith(RESOURCE_PREFIX + "-")]
            typer.echo(
                f"api         reachable; {len(runtimes)} runtime(s), {len(mine)} created by forkbench"
            )

    asyncio.run(probe())


@app.command()
def run(
    tasks: Annotated[
        list[Path], typer.Argument(help="Task folders (Harbor / Terminal-Bench layout).")
    ],
    trials: Annotated[int, typer.Option(help="Oracle trials per strategy per task.")] = 5,
    strategy: Annotated[list[str], typer.Option(help="scratch and/or checkpoint.")] = [  # noqa: B006
        "scratch",
        "checkpoint",
    ],
    concurrency: Annotated[int, typer.Option(min=1, max=8)] = 1,
    nop: Annotated[bool, typer.Option(help="Also run one no-op trial per strategy.")] = True,
    egress: Annotated[
        bool, typer.Option(help="Also run one egress-policy probe per strategy.")
    ] = True,
    budget: Annotated[
        float, typer.Option(help="Stop before estimated spend exceeds this (USD).")
    ] = 10.0,
    vcpus: int = 2,
    memory_mib: int = 4096,
    out_dir: Path = Path("results"),
    keep_checkpoints: bool = False,
    grade: Annotated[
        bool,
        typer.Option(
            help="Judge rewards (oracle=1, nop=0). Use --no-grade if a task's verifier is known-broken."
        ),
    ] = True,
    verbose: bool = False,
) -> None:
    """Run the benchmark. Every created runtime/checkpoint is deleted at the end (even on Ctrl-C)."""
    s = _settings(budget_usd=budget, vcpus=vcpus, memory_mib=memory_mib)
    try:
        specs = [load_task(p) for p in tasks]
    except TaskError as exc:
        typer.secho(f"task error: {exc}", fg="red", err=True)
        raise typer.Exit(2) from exc
    run_id = f"{RESOURCE_PREFIX}-{datetime.now(UTC):%m%d%H%M}-{secrets.token_hex(2)}"
    raw = out_dir / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    setup_logging(str(raw / f"{run_id}.log"), (s.token.get_secret_value(),), verbose)
    cfg = RunConfig(
        run_id=run_id,
        tasks=specs,
        out=raw / f"{run_id}.jsonl",
        strategies=tuple(strategy),
        trials=trials,
        nop=nop,
        egress=egress,
        concurrency=concurrency,
        keep_checkpoints=keep_checkpoints,
        grade=grade,
    )

    async def go() -> Path:
        async with _client(s) as client:
            return await Runner(cfg, s, client).run()

    try:
        jsonl = asyncio.run(go())
    except BudgetExceeded as exc:
        typer.secho(f"budget: {exc}", fg="yellow", err=True)
        raise typer.Exit(3) from exc
    md, page = write_reports(jsonl, out_dir / run_id)
    typer.echo(f"\nresults: {jsonl}\nsummary: {md}\nreport:  {page}")


@app.command()
def report(jsonl: Path, out_dir: Path = Path("results")) -> None:
    """Rebuild summary.md and report.html from a results file."""
    md, page = write_reports(jsonl, out_dir / jsonl.stem)
    typer.echo(f"summary: {md}\nreport:  {page}")


@app.command()
def cleanup(yes: Annotated[bool, typer.Option("--yes", help="Actually delete.")] = False) -> None:
    """Delete every runtime and checkpoint whose name starts with 'fb-' (leftovers from crashes)."""
    s = _settings()
    if not yes:
        typer.echo("dry run: pass --yes to delete all 'fb-*' runtimes and checkpoints.")
        raise typer.Exit(0)

    async def go() -> tuple[list[str], list[str]]:
        async with _client(s) as client:
            return await purge(client, RESOURCE_PREFIX + "-")

    runtimes, checkpoints = asyncio.run(go())
    typer.echo(f"deleted {len(runtimes)} runtime(s), {len(checkpoints)} checkpoint(s)")
    for item in runtimes + checkpoints:
        typer.echo(f"  {item}")
