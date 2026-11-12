"""
reproduce.py — one command for every number and chart in the write-up.

    .venv/Scripts/python scripts/reproduce.py            (RY)
    .venv/Scripts/python scripts/reproduce.py RY XIU

For each name it runs, in order:
  1. scripts/run_days.py    every day, three rate set-ups, the pooled call-put chart
  2. scripts/front_chart.py the front chart from what step 1 cached

Everything the two scripts print is also written to reports/<name>_numbers.txt.
Those are derived statistics only (vol points, basis points, counts), so the
file can live in git, and a reader without the licensed data can still check
the README and docs/findings.md against it.

It needs the raw files under data/ (see the README). The first run prices
every day and takes about a quarter of an hour for six months of one name on
eight cores; later runs load from the cache in seconds.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve().parent

STEPS = [("run_days.py", ["--compare-bills"]),
         ("front_chart.py", [])]


def run(script: str, name: str, extra: list[str]) -> str:
    """Run one script, echo its output as it comes, and return it."""
    cmd = [sys.executable, str(HERE / script), name, *extra]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace", cwd=ROOT)
    lines = []
    for line in proc.stdout:
        if "Warning" in line or line.lstrip().startswith(("return dtype", "per_day", "day =")):
            continue                                # library chatter, not results
        print(line, end="")
        lines.append(line)
    if proc.wait() != 0:
        raise SystemExit(f"{script} {name} failed; nothing was written")
    return "".join(lines)


def main(names: list[str]) -> None:
    for name in names:
        t0 = time.time()
        parts = [f"{name}: every reported number, from scripts/reproduce.py\n"
                 "Derived statistics only. No prices or quotes appear in this file.\n"]
        for script, extra in STEPS:
            parts.append(f"\n{'=' * 78}\n{script} {name} {' '.join(extra)}\n{'=' * 78}\n")
            parts.append(run(script, name, extra))
        out = ROOT / "reports" / f"{name.lower()}_numbers.txt"
        out.write_text("".join(parts), encoding="utf-8")
        print(f"\nnumbers: {out.relative_to(ROOT)}   ({time.time() - t0:.0f} s)")


if __name__ == "__main__":
    main(sys.argv[1:] or ["RY"])
