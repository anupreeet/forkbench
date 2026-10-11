"""Print the README headline table straight from benchmark results (no hand-copied numbers).

Usage: .venv/bin/python scripts/readme_table.py results/bench/all.jsonl
"""

import sys
from pathlib import Path

from forkbench.results import load, summarize

IMAGE_MB = {"regex-log": 29, "chess-best-move": 216, "build-cython-ext": 290, "qemu-startup": 1249}


def main(path: str) -> None:
    rows = load(Path(path))
    s = summarize(rows)
    print("| task (image) | reset from scratch | reset from checkpoint | saved per trial | "
          "one-off golden build | break-even | forks correct (graded) |")
    print("|---|---|---|---|---|---|---|")
    for task in IMAGE_MB:
        sc, ck = s["cells"].get(f"{task}/scratch"), s["cells"].get(f"{task}/checkpoint")
        if not sc or not ck:
            continue
        g = s["golden"].get(task, {})
        one_off = sum(v for k, v in g.items() if k.endswith("_s"))
        saved = sc["reset"].median - ck["reset"].median
        pct = 100 * saved / sc["reset"].median if sc["reset"].median else 0
        print(f"| {task} ({IMAGE_MB[task]} MB) | {sc['reset'].fmt()} | {ck['reset'].fmt()} | "
              f"{saved:.0f}s ({pct:.0f}%) | {one_off:.0f}s | {g.get('break_even_trials', '–')} trials | "
              f"checkpoint {ck['correct']}, scratch {sc['correct']} |")


if __name__ == "__main__":
    main(sys.argv[1])
