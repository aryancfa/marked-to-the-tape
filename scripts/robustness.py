"""
robustness.py — does any one choice drive the result? (build sheet B10)

    .venv/Scripts/python scripts/robustness.py RY

The front chart rests on choices: how unknown dividends are projected, how far
back the funding spread looks, how many steps the tree has, where the share
price comes from. Each is changed ON ITS OWN, on the same days, and the main
numbers are put side by side. A conclusion that holds in every row does not
depend on the choice. One that moves says which choice it depends on.

To keep the run to about forty minutes it uses every second trading day, and
every fourth for the finer tree, which costs four times as much per day. Each
row is compared with a baseline computed on exactly the same days.

Prints DERIVED numbers only and writes reports/<name>_robustness.txt.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve().parent

# label, flags, days stride. The baseline for a stride is the row with no flags.
VARIANTS = [
    ("baseline", [], 2),
    ("dividends: no raise projected", ["--flat-dividends"], 2),
    ("funding: latest 10 estimates only", ["--window", "10"], 2),
    ("funding: latest 20 estimates only", ["--window", "20"], 2),
    ("share price: 3 strike pairs", ["--spot-pairs", "3"], 2),
    ("share price: file's last trade", ["--spot-last"], 2),
    ("baseline (every 4th day)", [], 4),
    ("tree: 800 steps (every 4th day)", ["--steps", "800"], 4),
]
GROUPS = [("monthly over 60d, declared dividends", "long, declared"),
          ("monthly over 60d, projected dividend", "long, projected"),
          ("weekly up to 30d", "short weekly")]
MIN_HISTORY = {2: 10, 4: 5}       # earlier estimates needed, about 20 trading days either way


def run(name: str, flags: list[str], every: int, start: str | None) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "summary.json"
        cmd = [sys.executable, str(HERE / "run_days.py"), name, "--every", str(every),
               "--min-history", str(MIN_HISTORY[every]), "--summary-json", str(out), *flags]
        if start:
            cmd += ["--from-date", start]
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", cwd=ROOT)
        if r.returncode != 0 or not out.exists():
            raise SystemExit(f"run failed: {' '.join(flags) or 'baseline'}\n{r.stdout[-800:]}"
                             f"\n{r.stderr[-800:]}")
        return json.loads(out.read_text(encoding="utf-8"))


def main(name: str) -> None:
    lines: list[str] = []

    def say(text: str = "") -> None:
        print(text, flush=True)
        lines.append(text)

    say(f"{name}: one choice changed at a time. Call minus put implied vol in vol points,")
    say("with the funding spread applied; t = mean / standard error across days.")
    rows, start = [], {}
    for label, flags, every in VARIANTS:
        t0 = time.time()
        # the widest-window variant decides the first day everyone can report from
        res = run(name, flags, every, start.get(every))
        if not flags:
            start[every] = res["first"]
        funded = res.get("funded", True)
        row = {"choice": label if funded else label + " (NO spread applied)",
               "days": res["days"],
               "spread bp": res["spread_mean_bp"], "se": res["spread_se_bp"]}
        which = "funded" if funded else "base"
        for key, short in GROUPS:
            g = res["groups"].get(key, {})
            row[short] = g.get("gap_" + which)
            row[short + " t"] = g.get("t_" + which)
        rows.append(row)
        print(f"  done: {label} ({time.time() - t0:.0f} s)", flush=True)

    import pandas as pd
    table = pd.DataFrame(rows)
    say()
    say(table.round(2).to_string(index=False))
    say()
    say("spread bp = funding spread the options imply over the futures curve, a year.")
    say("NO spread applied = the spread fitted under that choice was too poorly measured to")
    say("use (standard error above 5 bp a year), so its gaps are shown with none applied.")
    say("long, declared   = monthly expiries over 60 days resting on declared dividends")
    say("long, projected  = the same resting on a projected dividend")
    say("short weekly     = weekly expiries up to 30 days")
    out = ROOT / "reports" / f"{name.lower()}_robustness.txt"
    out.write_text("Derived statistics only. No prices or quotes appear in this file.\n\n"
                   + "\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nsaved: {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "RY")
