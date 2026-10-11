"""Orchestrates a benchmark run: prepare -> interleaved trials -> cleanup. Budget-guarded."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from forkbench import results
from forkbench.log import get_logger
from forkbench.results import TrialRecord
from forkbench.sandbox import Gateway
from forkbench.settings import Settings
from forkbench.strategies import Checkpoint, Scratch, Strategy
from forkbench.taskspec import TaskSpec
from forkbench.workload import (
    clock_check,
    egress_until,
    exec_latency,
    identity,
    leak_check,
    run_workload,
)

log = get_logger("runner")

# Rough per-trial wall time used only for the up-front budget estimate (refined by real spend).
EST_TRIAL_MIN = 6.0
EST_GOLDEN_MIN = 5.0


class BudgetExceeded(RuntimeError):
    pass


@dataclass
class RunConfig:
    run_id: str
    tasks: list[TaskSpec]
    out: Path
    strategies: tuple[str, ...] = ("scratch", "checkpoint")
    trials: int = 5
    nop: bool = True
    egress: bool = True
    concurrency: int = 1
    keep_checkpoints: bool = False
    grade: bool = True  # False: record rewards but don't judge them (e.g. verifier broken upstream)

    def jobs_per_task(self) -> list[tuple[str, str, int]]:
        """Interleave strategies (A,B / B,A / ...) so time-of-day drift can't favour one side."""
        jobs: list[tuple[str, str, int]] = []
        for i in range(self.trials):
            order = self.strategies if i % 2 == 0 else tuple(reversed(self.strategies))
            jobs += [(s, "oracle", i) for s in order]
        if self.nop:
            jobs += [(s, "nop", 0) for s in self.strategies]
        if self.egress:
            jobs += [(s, "egress", 0) for s in self.strategies]
        return jobs


@dataclass
class Budget:
    limit_usd: float
    usd_per_s: float
    spent_usd: float = 0.0
    events: list[str] = field(default_factory=list)

    def estimate(self, cfg: RunConfig) -> float:
        minutes = len(cfg.tasks) * (
            len(cfg.jobs_per_task()) * EST_TRIAL_MIN
            + (EST_GOLDEN_MIN if "checkpoint" in cfg.strategies else 0)
        )
        return minutes * 60 * self.usd_per_s

    def charge(self, seconds: float) -> float:
        cost = seconds * self.usd_per_s
        self.spent_usd += cost
        return cost

    def check(self) -> None:
        if self.spent_usd >= self.limit_usd:
            raise BudgetExceeded(
                f"estimated spend ${self.spent_usd:.2f} reached budget ${self.limit_usd:.2f}"
            )


def _deny_all() -> Any:
    from runta import EgressMode, EgressPolicy

    return EgressPolicy(mode=EgressMode.ALLOWLIST, allowed_hosts=[], denied_hosts=[])


def _open() -> Any:
    from runta import EgressPolicy

    return EgressPolicy()  # documented reset: empty denylist = open


class Runner:
    def __init__(self, cfg: RunConfig, settings: Settings, client: Any) -> None:
        self.cfg = cfg
        self.settings = settings
        self.gw = Gateway(client, settings.vcpus, settings.memory_mib, settings.step_timeout_s)
        self.budget = Budget(settings.budget_usd, settings.usd_per_second())

    def _strategy(self, name: str) -> Strategy:
        if name == "scratch":
            return Scratch(self.gw, self.settings.step_timeout_s)
        if name == "checkpoint":
            return Checkpoint(self.gw, self.settings.step_timeout_s)
        raise ValueError(f"unknown strategy {name!r}")

    async def run(self) -> Path:
        estimate = self.budget.estimate(self.cfg)
        if estimate > self.budget.limit_usd:
            raise BudgetExceeded(f"estimated ${estimate:.2f} > budget ${self.budget.limit_usd:.2f}")
        log.info(
            "run %s: %d task(s), estimated ≤ $%.2f", self.cfg.run_id, len(self.cfg.tasks), estimate
        )
        try:
            for task in self.cfg.tasks:
                await self._run_task(task)
        finally:
            failures = await asyncio.shield(
                self.gw.cleanup(keep_checkpoints=self.cfg.keep_checkpoints)
            )
            if failures:
                log.warning("cleanup left %d item(s); run `forkbench cleanup`", len(failures))
        log.info("done: estimated spend $%.3f -> %s", self.budget.spent_usd, self.cfg.out)
        return self.cfg.out

    async def _run_task(self, task: TaskSpec) -> None:
        strategies = {name: self._strategy(name) for name in self.cfg.strategies}
        try:
            for strat in strategies.values():
                await self._prepare(strat, task)
            sem = asyncio.Semaphore(self.cfg.concurrency)

            async def guarded(job: tuple[str, str, int]) -> None:
                async with sem:
                    self.budget.check()
                    await self._trial(strategies[job[0]], task, job[1], job[2])

            if self.cfg.concurrency == 1:
                for job in self.cfg.jobs_per_task():
                    await guarded(job)
            else:
                await asyncio.gather(*(guarded(j) for j in self.cfg.jobs_per_task()))
        finally:
            if not self.cfg.keep_checkpoints:
                for strat in strategies.values():
                    await strat.teardown()

    async def _prepare(self, strat: Strategy, task: TaskSpec) -> None:
        started = datetime.now(UTC).isoformat()
        t0 = time.monotonic()
        rec = TrialRecord(self.cfg.run_id, task.name, strat.name, "prepare", 0, started)
        try:
            rec.timings = await strat.prepare(task, self.cfg.run_id)
        except Exception as exc:
            rec.error = f"{type(exc).__name__}: {exc}"[:800]
            raise
        finally:
            if rec.timings or rec.error:
                rec.cost_usd_est = self.budget.charge(time.monotonic() - t0)
                results.append(self.cfg.out, rec)
                log.info("prepare %s/%s %s", task.name, strat.name, rec.error or rec.timings)

    async def _trial(self, strat: Strategy, task: TaskSpec, kind: str, i: int) -> None:
        name = f"{self.cfg.run_id}-{task.name}-{strat.name[:5]}-{kind}-{i}"
        rec = TrialRecord(
            self.cfg.run_id, task.name, strat.name, kind, i, datetime.now(UTC).isoformat()
        )
        rec.expected_reward = {"oracle": 1.0, "nop": 0.0}.get(kind) if self.cfg.grade else None
        t0 = time.monotonic()
        acquired = None
        try:
            policy = _deny_all() if (kind == "egress" and strat.name == "checkpoint") else None
            acquired = await strat.acquire(task, name, egress_policy=policy)
            sb = acquired.sandbox
            rec.runtime_id = sb.id
            rec.timings.update(acquired.timings)
            rec.checks.update(acquired.notes)
            rec.checks["leak"] = await leak_check(sb, name)
            rec.checks["clock"] = await clock_check(sb)
            rec.checks["identity"] = await identity(sb)
            if kind == "egress":
                rec.checks["egress"] = await self._egress(sb, strat.name)
            else:
                rec.checks["exec_latency"] = await exec_latency(sb)
                outcome = await run_workload(sb, task, kind)
                rec.reward = outcome["reward"]
                rec.timings.update(outcome["timings"])
                rec.checks["verifier_tail"] = outcome["verifier_tail"]
                if outcome.get("solve_tail"):
                    rec.checks["solve_tail"] = outcome["solve_tail"]
        except Exception as exc:
            rec.error = f"{type(exc).__name__}: {exc}"[:800]
        finally:
            if acquired is not None:
                try:
                    rec.timings["delete_s"] = await self.gw.delete(acquired.sandbox)
                except Exception as exc:  # noqa: BLE001 - recorded; final cleanup retries
                    rec.error = (rec.error or "") + f" | delete failed: {exc!r}"
            alive = time.monotonic() - t0
            rec.timings["total_s"] = alive
            rec.cost_usd_est = self.budget.charge(alive)
            results.append(self.cfg.out, rec)
            status = (
                rec.error or f"reward={rec.reward} reset={rec.reset_s and round(rec.reset_s, 1)}s"
            )
            log.info("%-16s %-10s %-6s #%d  %s", task.name, strat.name, kind, i, status)

    async def _egress(self, sb: Any, strategy: str) -> dict[str, Any]:
        """Negative and positive controls, each polled until it takes effect (or 120 s)."""
        if strategy == "checkpoint":
            at_restore = await egress_until(sb, want_open=False)  # policy given to the restore call
            await sb.runtime.egress.set_policy(_open())
            reopened = await egress_until(sb, want_open=True)
            return {"deny_all_at_restore": at_restore, "after_change_to_open": reopened}
        before = await egress_until(sb, want_open=True, limit_s=0)
        await sb.runtime.egress.set_policy(_deny_all())
        after = await egress_until(sb, want_open=False)
        return {"open_before": before, "after_change_to_deny_all": after}
