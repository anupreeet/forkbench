"""Turn results JSONL into summary.md and a self-contained report.html (no JS, no CDN)."""

from __future__ import annotations

import html
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

from forkbench.results import Spread, load, summarize


def _fmt_egress(phases: dict[str, Any]) -> str:
    parts = []
    for phase, r in phases.items():
        if not isinstance(r, dict) or "want" not in r:
            continue
        verdict = "ok" if r.get("matched") else f"NOT reached (last {r.get('last')})"
        parts.append(f"{phase}: {r['want']} {verdict} after {r.get('after_s')}s")
    return ", ".join(parts)


def checks_summary(rows: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    by: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        if r["kind"] != "prepare" and not r["error"]:
            by[r["strategy"]].append(r)
    out: dict[str, dict[str, str]] = {}
    for strat, rs in sorted(by.items()):
        leaks = [r["checks"].get("leak", {}).get("clean") for r in rs if "leak" in r["checks"]]
        skews = [abs(r["checks"]["clock"]["skew_s"]) for r in rs if "clock" in r["checks"]]
        overheads = [
            r["checks"]["exec_latency"]["wall_median_s"]
            for r in rs
            if "exec_latency" in r["checks"]
        ]
        ids = [r["checks"]["identity"] for r in rs if "identity" in r["checks"]]
        restarted = [
            r["checks"].get("container_restarted")
            for r in rs
            if "container_restarted" in r["checks"]
        ]
        egress = [r["checks"]["egress"] for r in rs if "egress" in r["checks"]]
        n = len(ids)
        out[strat] = {
            "trials checked": str(len(rs)),
            "no leftover state": f"{sum(1 for x in leaks if x)}/{len(leaks)}",
            "max |clock skew|": f"{max(skews):.2f}s" if skews else "–",
            "no-op exec round trip (median)": f"{statistics.median(overheads):.2f}s"
            if overheads
            else "–",
            "container had to be restarted": f"{sum(1 for x in restarted if x)}/{len(restarted)}"
            if restarted
            else "n/a",
            "unique boot_id / machine-id / hostname / fresh uuid": " / ".join(
                f"{len({i.get(k) for i in ids})}/{n}"
                for k in ("boot_id", "machine_id", "hostname", "fresh_uuid")
            )
            if n
            else "–",
            "egress (want → result, time to take effect)": "; ".join(_fmt_egress(e) for e in egress)
            or "–",
        }
    return out


def _cell_rows(summary: dict[str, Any]) -> list[list[str]]:
    rows = []
    for c in summary["cells"].values():
        rows.append(
            [
                c["task"],
                c["strategy"],
                c["reset"].fmt(),
                c["provision"].fmt(),
                c["solve"].fmt(),
                c["verify"].fmt(),
                c["correct"],
                str(c["verifier_failed"]),
                str(c["errors"]),
                f"${c['cost_usd_est']:.3f}",
            ]
        )
    return rows


HEAD = [
    "task",
    "strategy",
    "reset (median, range, n)",
    "provision",
    "solve",
    "verify",
    "correct",
    "verifier failed",
    "errors",
    "est. cost",
]


def to_markdown(rows: list[dict[str, Any]]) -> str:
    s = summarize(rows)
    lines = [
        "# forkbench results",
        "",
        "`reset` = time until the task container accepts commands "
        "(provision + image pull + container start). Median (min–max, n).",
        "",
        "| " + " | ".join(HEAD) + " |",
        "|" + "---|" * len(HEAD),
    ]
    lines += ["| " + " | ".join(r) + " |" for r in _cell_rows(s)]
    if s["golden"]:
        lines += [
            "",
            "## One-off checkpoint cost per task",
            "",
            "| task | golden build + checkpoint | break-even trials |",
            "|---|---|---|",
        ]
        for task, g in s["golden"].items():
            one_off = sum(v for k, v in g.items() if k.endswith("_s"))
            lines.append(
                f"| {task} | {one_off:.0f}s (checkpoint {g.get('checkpoint_s', 0):.0f}s) "
                f"| {g.get('break_even_trials', '–')} |"
            )
    lines += ["", "## Correctness and isolation checks", ""]
    for strat, checks in checks_summary(rows).items():
        lines += [f"**{strat}**", ""] + [f"- {k}: {v}" for k, v in checks.items()] + [""]
    return "\n".join(lines) + "\n"


def _bars_svg(summary: dict[str, Any]) -> str:
    cells = list(summary["cells"].values())
    if not cells:
        return ""
    vmax = max((c["reset"].hi for c in cells if c["reset"].n), default=1.0) or 1.0
    row_h, left, width = 26, 230, 460
    parts = [
        f'<svg viewBox="0 0 {left + width + 90} {row_h * len(cells) + 10}" role="img" '
        f'aria-label="Median reset time per task and strategy">'
    ]
    for i, c in enumerate(cells):
        sp: Spread = c["reset"]
        y = 5 + i * row_h
        label = html.escape(f"{c['task']} · {c['strategy']}")
        cls = "ck" if c["strategy"] == "checkpoint" else "sc"
        w = width * sp.median / vmax
        lo, hi = left + width * sp.lo / vmax, left + width * sp.hi / vmax
        parts.append(f'<text x="{left - 8}" y="{y + 16}" text-anchor="end">{label}</text>')
        parts.append(
            f'<rect class="{cls}" x="{left}" y="{y + 4}" width="{w:.1f}" height="16" rx="2"/>'
        )
        parts.append(f'<line class="rng" x1="{lo:.1f}" x2="{hi:.1f}" y1="{y + 12}" y2="{y + 12}"/>')
        parts.append(f'<text x="{left + w + 6:.1f}" y="{y + 16}">{sp.median:.0f}s</text>')
    parts.append("</svg>")
    return "".join(parts)


def to_html(rows: list[dict[str, Any]], title: str = "forkbench results") -> str:
    s = summarize(rows)
    table = "".join(
        "<tr>" + "".join(f"<td>{html.escape(x)}</td>" for x in r) + "</tr>" for r in _cell_rows(s)
    )
    checks = "".join(
        f"<h3>{html.escape(strat)}</h3><ul>"
        + "".join(f"<li><b>{html.escape(k)}</b>: {html.escape(v)}</li>" for k, v in c.items())
        + "</ul>"
        for strat, c in checks_summary(rows).items()
    )
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(title)}</title>
<style>
:root{{--bg:#fff;--fg:#1d1d1f;--mut:#6e6e73;--line:#e5e5ea;--sc:#8e8e93;--ck:#0a84ff}}
@media (prefers-color-scheme:dark){{:root{{--bg:#111;--fg:#f2f2f7;--mut:#a1a1a6;--line:#2c2c2e;--sc:#636366}}}}
body{{background:var(--bg);color:var(--fg);font:15px/1.5 -apple-system,system-ui,sans-serif;
max-width:980px;margin:32px auto;padding:0 16px}}
table{{border-collapse:collapse;width:100%;font-size:13px}}td,th{{border-bottom:1px solid var(--line);
padding:6px 8px;text-align:left}}th{{color:var(--mut);font-weight:500}}
svg{{width:100%;height:auto;font-size:12px;fill:var(--fg)}}.sc{{fill:var(--sc)}}.ck{{fill:var(--ck)}}
.rng{{stroke:var(--fg);stroke-width:1.5;opacity:.6}}.mut{{color:var(--mut)}}
.wrap{{overflow-x:auto}}
</style></head><body>
<h1>{html.escape(title)}</h1>
<p class="mut">Reset = time until the task container accepts commands. Bars show the median; the line
shows min–max. Costs are estimates from published rates.</p>
{_bars_svg(s)}
<div class="wrap"><table><tr>{"".join(f"<th>{h}</th>" for h in HEAD)}</tr>{table}</table></div>
<h2>Correctness and isolation checks</h2>{checks}
</body></html>"""


def write_reports(jsonl: Path, out_dir: Path) -> tuple[Path, Path]:
    rows = load(jsonl)
    out_dir.mkdir(parents=True, exist_ok=True)
    md, page = out_dir / "summary.md", out_dir / "report.html"
    md.write_text(to_markdown(rows))
    page.write_text(to_html(rows))
    return md, page
