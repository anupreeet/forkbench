"""Result records (one JSON line per trial) and honest summary statistics."""

from __future__ import annotations

import json
import re
import statistics
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

_PYTEST_SUMMARY = re.compile(r"=+ .*\b(passed|failed|error|errors)\b.* in [0-9.]+s")


@dataclass
class TrialRecord:
    run_id: str
    task: str
    strategy: str  # scratch | checkpoint
    kind: str  # oracle | nop | egress
    trial: int
    started_utc: str
    runtime_id: str | None = None
    reward: float | None = None
    expected_reward: float | None = None
    timings: dict[str, float] = field(default_factory=dict)
    checks: dict[str, Any] = field(default_factory=dict)
    cost_usd_est: float = 0.0
    error: str | None = None

    @property
    def reset_s(self) -> float | None:
        t = self.timings
        if "provision_s" not in t:
            return None
        return t["provision_s"] + t.get("pull_s", 0.0) + t.get("container_s", 0.0)

    @property
    def verifier_ran(self) -> bool | None:
        """Did the test framework actually run? (pytest prints a '=== N passed/failed in Xs ===' line.)

        Without this, a no-op trial whose verifier crashed (e.g. a failed download of the test
        tooling) would score 0 and look "correct" for the wrong reason."""
        tail = self.checks.get("verifier_tail")
        if tail is None:
            return None
        return bool(_PYTEST_SUMMARY.search(str(tail)))

    @property
    def correct(self) -> bool | None:
        if self.expected_reward is None or self.verifier_ran is not True or self.reward is None:
            return (
                None  # not gradable: no expectation, verifier didn't (provably) run, or no reward
            )
        return self.reward is not None and abs(self.reward - self.expected_reward) < 1e-9

    def to_json(self) -> str:
        row = asdict(self)
        row["reset_s"] = self.reset_s
        row["correct"] = self.correct
        row["verifier_ran"] = self.verifier_ran
        return json.dumps(row, default=str)


def append(path: Path, record: TrialRecord) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        fh.write(record.to_json() + "\n")


_FIELDS = (
    "run_id",
    "task",
    "strategy",
    "kind",
    "trial",
    "started_utc",
    "runtime_id",
    "reward",
    "expected_reward",
    "timings",
    "checks",
    "cost_usd_est",
    "error",
)


def load(path: Path) -> list[dict[str, Any]]:
    """Load rows and re-derive grading from the raw facts, so the report always applies the current
    rules (never stale `correct` values written by an older version of the tool)."""
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    for row in rows:
        rec = TrialRecord(**{k: row.get(k) for k in _FIELDS if k in row})
        if rec.timings is None:
            rec.timings = {}
        if rec.checks is None:
            rec.checks = {}
        row["reset_s"], row["verifier_ran"], row["correct"] = (
            rec.reset_s,
            rec.verifier_ran,
            rec.correct,
        )
    return rows


@dataclass(frozen=True)
class Spread:
    n: int
    median: float
    lo: float
    hi: float

    def fmt(self, unit: str = "s") -> str:
        if self.n == 0:
            return "–"
        return f"{self.median:.1f}{unit} ({self.lo:.1f}–{self.hi:.1f}, n={self.n})"


def spread(values: list[float]) -> Spread:
    """Median and range only: with n≈5 per cell, percentiles like p95 would be meaningless."""
    vals = [v for v in values if v is not None]
    if not vals:
        return Spread(0, 0.0, 0.0, 0.0)
    return Spread(len(vals), statistics.median(vals), min(vals), max(vals))


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Per (task, strategy): reset/solve/verify/total spreads, correctness, cost; per task: golden cost."""
    cells: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    golden: dict[str, dict[str, float]] = {}
    for r in rows:
        if r["kind"] == "prepare":
            golden[r["task"]] = r["timings"]
            continue
        cells[(r["task"], r["strategy"])].append(r)

    out: dict[str, Any] = {"cells": {}, "golden": golden}
    for (task, strat), rs in sorted(cells.items()):
        oracle = [r for r in rs if r["kind"] == "oracle" and not r["error"]]
        graded = [r for r in rs if r.get("correct") is not None]
        out["cells"][f"{task}/{strat}"] = {
            "task": task,
            "strategy": strat,
            "reset": spread([r["reset_s"] for r in oracle]),
            "provision": spread([r["timings"].get("provision_s") for r in oracle]),
            "verify": spread([r["timings"].get("verify_s") for r in oracle]),
            "solve": spread([r["timings"].get("solve_s") for r in oracle]),
            "correct": f"{sum(1 for r in graded if r['correct'])}/{len(graded)}",
            "verifier_failed": sum(1 for r in rs if r.get("verifier_ran") is False),
            "errors": sum(1 for r in rs if r["error"]),
            "cost_usd_est": round(sum(r["cost_usd_est"] for r in rs), 4),
        }
    for task, g in golden.items():
        scratch = out["cells"].get(f"{task}/scratch")
        ckpt = out["cells"].get(f"{task}/checkpoint")
        if scratch and ckpt and scratch["reset"].n and ckpt["reset"].n:
            saved = scratch["reset"].median - ckpt["reset"].median
            one_off = sum(v for k, v in g.items() if k.endswith("_s"))
            g["break_even_trials"] = round(one_off / saved, 1) if saved > 0 else float("inf")
    return out
