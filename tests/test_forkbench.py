"""Unit tests: run with `uv run pytest`. No network, no Runta credit spent."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

import pytest
from pydantic import SecretStr

from forkbench import results
from forkbench.log import RedactingFilter, redact
from forkbench.report import to_html, to_markdown
from forkbench.runner import Budget, BudgetExceeded, RunConfig, Runner
from forkbench.settings import Settings, load_settings
from forkbench.taskspec import TaskError, load_task
from tests.fakes import FakeClient

TOKEN = "rt_live_abcdefghijklmnopqrstuvwxyz0123456789"


@pytest.fixture
def task_dir(tmp_path: Path) -> Path:
    d = tmp_path / "demo-task"
    (d / "solution").mkdir(parents=True)
    (d / "tests").mkdir()
    (d / "environment").mkdir()
    (d / "task.toml").write_text(
        '[environment]\ndocker_image = "ghcr.io/example/demo:1"\n'
        "[agent]\ntimeout_sec = 60.0\n[verifier]\ntimeout_sec = 60.0\n"
    )
    (d / "solution" / "solve.sh").write_text("echo solve\n")
    (d / "tests" / "test.sh").write_text("echo test\n")
    return d


def settings(budget: float = 10.0) -> Settings:
    return Settings(token=SecretStr(TOKEN), budget_usd=budget)


def make_cfg(tmp_path: Path, task_dir: Path, **kw: object) -> RunConfig:
    defaults: dict[str, object] = dict(trials=2, nop=True, egress=True)
    defaults.update(kw)
    return RunConfig(
        run_id="fb-test", tasks=[load_task(task_dir)], out=tmp_path / "out.jsonl", **defaults
    )  # type: ignore[arg-type]


# --- task parsing -------------------------------------------------------------------------------


def test_load_task_reads_image_and_timeouts(task_dir: Path) -> None:
    spec = load_task(task_dir)
    assert spec.name == "demo-task"
    assert spec.docker_image == "ghcr.io/example/demo:1"
    assert spec.agent_timeout_s == 60.0


def test_load_task_requires_prebuilt_image(task_dir: Path) -> None:
    (task_dir / "task.toml").write_text("[environment]\n")
    with pytest.raises(TaskError, match="docker_image"):
        load_task(task_dir)


# --- end-to-end with the fake SDK ---------------------------------------------------------------


async def test_full_run_rewards_checks_and_cleanup(tmp_path: Path, task_dir: Path) -> None:
    client = FakeClient()
    cfg = make_cfg(tmp_path, task_dir)
    await Runner(cfg, settings(), client).run()

    rows = results.load(cfg.out)
    oracle = [r for r in rows if r["kind"] == "oracle"]
    nop = [r for r in rows if r["kind"] == "nop"]
    assert len(oracle) == 4 and all(r["reward"] == 1.0 and r["correct"] for r in oracle)
    assert len(nop) == 2 and all(r["reward"] == 0.0 and r["correct"] for r in nop)
    assert all(r["error"] is None for r in rows), [r["error"] for r in rows]
    # every fork starts clean even though earlier forks wrote markers
    assert all(r["checks"]["leak"]["clean"] for r in rows if "leak" in r["checks"])
    # egress probes: positive and negative controls for both strategies
    eg = {r["strategy"]: r["checks"]["egress"] for r in rows if r["kind"] == "egress"}
    assert eg["scratch"]["open_before"]["last"]["host"] == "OPEN"
    assert eg["scratch"]["after_change_to_deny_all"]["matched"] is True
    assert eg["checkpoint"]["deny_all_at_restore"]["last"]["container"].startswith("BLOCKED")
    assert eg["checkpoint"]["after_change_to_open"]["matched"] is True
    # nothing left running, checkpoint deleted, golden builder deleted right after checkpointing
    assert client.alive() == [] and client.saved == {}
    assert client.calls.index("checkpoint") < client.calls.index("delete")


async def test_egress_check_waits_for_a_delayed_policy(
    tmp_path: Path, task_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("forkbench.workload.asyncio.sleep", _no_sleep)
    client = FakeClient()
    client.policy_delay_probes = 3  # policy change only takes effect on the 3rd probe
    cfg = make_cfg(tmp_path, task_dir, strategies=("scratch",), trials=0, nop=False)
    await Runner(cfg, settings(), client).run()
    (row,) = results.load(cfg.out)
    after = row["checks"]["egress"]["after_change_to_deny_all"]
    assert after["matched"] is True and after["samples"] == 3


async def _no_sleep(_: float) -> None:
    return None


async def test_crashed_verifier_is_not_graded(tmp_path: Path, task_dir: Path) -> None:
    """A nop whose verifier crashed scores 0 but must not count as 'correct'."""
    client = FakeClient()
    client.verifier_crash = True
    cfg = make_cfg(tmp_path, task_dir, strategies=("scratch",), trials=1, egress=False)
    await Runner(cfg, settings(), client).run()
    rows = results.load(cfg.out)
    assert all(r["verifier_ran"] is False and r["correct"] is None for r in rows)
    cell = results.summarize(rows)["cells"]["demo-task/scratch"]
    assert cell["correct"] == "0/0" and cell["verifier_failed"] == 2


def test_load_regrades_stale_rows(tmp_path: Path) -> None:
    row = {
        "run_id": "r",
        "task": "t",
        "strategy": "checkpoint",
        "kind": "oracle",
        "trial": 0,
        "started_utc": "now",
        "reward": None,
        "expected_reward": 1.0,
        "timings": {},
        "checks": {},
        "cost_usd_est": 0.0,
        "error": "ConnectionClosedError",
        "correct": False,
    }
    f = tmp_path / "old.jsonl"
    f.write_text(json.dumps(row) + "\n")
    assert results.load(f)[0]["correct"] is None  # stale False is re-derived as "not gradable"


def test_pytest_summary_detection() -> None:
    rec = results.TrialRecord("r", "t", "s", "oracle", 0, "now")
    for line, ran in [
        ("===== 6 passed in 0.12s =====", True),
        ("==== 2 failed, 4 passed in 1.23s (0:00:01) ====", True),
        ("uv: command not found", False),
    ]:
        rec.checks["verifier_tail"] = line
        assert rec.verifier_ran is ran


async def test_failed_delete_is_retried_by_final_cleanup(tmp_path: Path, task_dir: Path) -> None:
    """A delete that fails mid-run (network blip) must not leak the runtime."""
    client = FakeClient()
    client.delete_failures = 1  # the first trial's delete fails once
    cfg = make_cfg(tmp_path, task_dir, strategies=("scratch",), trials=2, nop=False, egress=False)
    await Runner(cfg, settings(), client).run()
    rows = results.load(cfg.out)
    assert "delete failed" in (rows[0]["error"] or "")
    assert client.alive() == []  # final cleanup retried and succeeded


def test_errored_trial_is_not_graded() -> None:
    rec = results.TrialRecord(
        "r",
        "t",
        "checkpoint",
        "oracle",
        0,
        "now",
        expected_reward=1.0,
        reward=None,
        error="ConnectionClosedError: keepalive ping timeout",
    )
    assert rec.correct is None


async def test_trials_alternate_strategy_order(tmp_path: Path, task_dir: Path) -> None:
    cfg = make_cfg(tmp_path, task_dir, trials=3, nop=False, egress=False)
    order = [(s, i) for s, kind, i in cfg.jobs_per_task()]
    assert order == [
        ("scratch", 0),
        ("checkpoint", 0),
        ("checkpoint", 1),
        ("scratch", 1),
        ("scratch", 2),
        ("checkpoint", 2),
    ]


async def test_failed_setup_deletes_runtime_immediately(tmp_path: Path, task_dir: Path) -> None:
    client = FakeClient()
    client.fail_exec["docker pull"] = "pull access denied"
    cfg = make_cfg(tmp_path, task_dir, strategies=("scratch",), trials=2, nop=False, egress=False)
    await Runner(cfg, settings(), client).run()
    rows = results.load(cfg.out)
    assert all("pull access denied" in r["error"] for r in rows)
    # the half-built runtime is deleted right away, not at the end of the whole run
    lifecycle = [c for c in client.calls if c in ("create", "delete")]
    assert lifecycle == ["create", "delete", "create", "delete"]
    assert client.alive() == []


async def test_restored_container_that_stopped_is_restarted(tmp_path: Path, task_dir: Path) -> None:
    client = FakeClient()
    client.restored_container_stopped = True
    cfg = make_cfg(
        tmp_path, task_dir, strategies=("checkpoint",), trials=1, nop=False, egress=False
    )
    await Runner(cfg, settings(), client).run()
    trial = [r for r in results.load(cfg.out) if r["kind"] == "oracle"][0]
    assert trial["checks"]["container_restarted"] is True and trial["reward"] == 1.0


async def test_cancel_mid_run_still_cleans_up(tmp_path: Path, task_dir: Path) -> None:
    client = FakeClient()
    cfg = make_cfg(tmp_path, task_dir, trials=50, nop=False, egress=False)
    runner = Runner(cfg, settings(budget=1000), client)
    task = asyncio.create_task(runner.run())
    while len(client.runtimes_made) < 3:
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert client.alive() == [] and client.saved == {}


async def test_budget_refuses_before_creating_anything(tmp_path: Path, task_dir: Path) -> None:
    client = FakeClient()
    cfg = make_cfg(tmp_path, task_dir, trials=500)
    with pytest.raises(BudgetExceeded):
        await Runner(cfg, settings(budget=1.0), client).run()
    assert client.calls == []


def test_budget_stops_new_trials_once_spent() -> None:
    b = Budget(limit_usd=0.01, usd_per_s=0.001)
    b.charge(5)
    b.check()
    b.charge(5)
    with pytest.raises(BudgetExceeded):
        b.check()


# --- secrets ------------------------------------------------------------------------------------


def test_redact_masks_token_shapes_and_exact_value() -> None:
    assert TOKEN not in redact(f"auth failed for {TOKEN}")
    assert "s3cr3t" not in redact("value=s3cr3t", ("s3cr3t",))


def test_logging_filter_redacts(caplog: pytest.LogCaptureFixture) -> None:
    logger = logging.getLogger("forkbench.test")
    logger.addFilter(RedactingFilter((TOKEN,)))
    with caplog.at_level(logging.INFO, logger="forkbench.test"):
        logger.info("token is %s", TOKEN)
        logger.info("run %s: %d task(s), estimated <= $%.2f", "fb-x", 3, 0.25)
    assert TOKEN not in caplog.text
    assert "run fb-x: 3 task(s), estimated <= $0.25" in caplog.text


def test_settings_never_expose_token_in_repr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("RUNTA_TOKEN", raising=False)
    env = tmp_path / ".env"
    env.write_text(f"RUNTA_TOKEN={TOKEN}\n")
    s = load_settings(env)
    assert s.token.get_secret_value() == TOKEN
    assert TOKEN not in repr(s) and TOKEN not in str(s)


# --- stats & report -----------------------------------------------------------------------------


def test_spread_uses_median_and_range_only() -> None:
    sp = results.spread([10.0, 30.0, 20.0, None])  # type: ignore[list-item]
    assert (sp.n, sp.median, sp.lo, sp.hi) == (3, 20.0, 10.0, 30.0)


async def test_report_renders_without_secrets(tmp_path: Path, task_dir: Path) -> None:
    cfg = make_cfg(tmp_path, task_dir)
    await Runner(cfg, settings(), FakeClient()).run()
    rows = results.load(cfg.out)
    md, page = to_markdown(rows), to_html(rows)
    assert "demo-task" in md and "break-even" in md and "<svg" in page
    assert TOKEN not in md + page
