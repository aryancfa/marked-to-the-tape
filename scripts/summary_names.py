"""
summary_names.py — one table across every name that has been run (B3, B5).

Run scripts/run_days.py for each name first; this reads what it cached.

    .venv/Scripts/python scripts/summary_names.py
    .venv/Scripts/python scripts/summary_names.py RY TD XIU

Per name: how many days, how far the share price implied from the options
sits from the file's last trade, the funding spread the options imply (with
a standard error that allows for dependence between days), what is left of
the call-put gap on long monthly expiries once that spread is applied, and
the share of rows rejected.

Reading it. The share-price column says whether the options and the tape
agree on where the share was. The funding column says what financing the
position costs, name by name; one firm's funding cost should be much the
same on every name, so differences are dividends or borrow speaking. The
"declared" gap tests rates and funding. The "projected" gap tests my
dividend projections and nothing else.

Prints DERIVED numbers only and writes reports/all_names_summary.txt.
"""

from __future__ import annotations

import pickle
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
import pandas as pd

from run_days import fingerprint, groups, mx_files, one_per_day, tested_rows
from surface import mean_std_error

DECLARED = "monthly over 60d, declared dividends"
PROJECTED = "monthly over 60d, projected dividend"
ORDER = ["RY", "TD", "BNS", "BMO", "MFC", "CNQ", "SU", "ENB", "CNR", "BCE", "ABX", "SHOP",
         "XIU", "ZEB", "XEG"]


def cached(name: str, pattern: str) -> list[dict]:
    """Day results this project cached itself (never anyone else's pickles)."""
    folder = ROOT / "data/derived" / name.lower() / fingerprint(name)
    files = [f for f in sorted(folder.glob(pattern)) if "bills" not in f.name] \
        if folder.exists() else []
    out = [pickle.loads(f.read_bytes()) for f in files]
    return one_per_day([r for r in out if r.get("ok")], f"{name} {pattern}")


def one(name: str) -> dict | None:
    base = cached(name, "*_base.pkl")
    if not base:
        return None
    funded = cached(name, "*_base_s*.pkl")
    est = pd.Series([r["funding_spread"] for r in base
                     if r["funding_spread"] is not None]) * 1e4
    tally = pd.DataFrame([r["tally"] for r in base]).fillna(0).sum()
    last = pd.Series([r["bp_vs_last"] for r in base])
    row = {"name": name, "days": len(base),
           "spot vs last, mean bp": last.mean(), "sd": last.std(),
           "funding bp": est.mean() if len(est) else np.nan,
           "se": mean_std_error(est) if len(est) > 1 else np.nan,
           "est. days": len(est),
           "rejected %": 100.0 * (1.0 - tally.get("ok", 0) / tally.sum())}
    kinds = sorted({(r.get("spread_source") or "own").split(",")[0].split(" ")[0]
                    for r in funded})
    use, label = (funded, " + ".join(kinds) + " spread") if funded else (base, "no spread")
    g = groups(tested_rows(use))
    for key, short in ((DECLARED, "declared"), (PROJECTED, "projected")):
        if key in g.index:
            row[f"{short} gap"], row[f"{short} t"] = g.loc[key, "gap"], g.loc[key, "t"]
            row[f"{short} days"] = int(g.loc[key, "days"])
    row["rates"] = label
    return row


def main(names: list[str]) -> None:
    rows = [r for r in (one(n) for n in names if mx_files(n)) if r]
    if not rows:
        raise SystemExit("nothing cached; run scripts/run_days.py for a name first")
    t = pd.DataFrame(rows).set_index("name")
    lines = ["Derived statistics only. No prices or quotes appear in this file.", "",
             "Every name run so far. Gaps are call minus put implied vol in vol points on",
             "monthly expiries over 60 days out, weighted by quote tightness, after the name's",
             "own trailing funding spread where one could be fitted. t uses a standard error",
             "that allows a day to resemble the days before it.", "",
             t.round(2).to_string(), ""]
    est = t["funding bp"].dropna()
    w = 1.0 / t.loc[est.index, "se"] ** 2
    lines += [f"Funding spread across {len(est)} names: median {est.median():.1f} bp a year, "
              f"range {est.min():.1f} to {est.max():.1f}, precision-weighted mean "
              f"{(est * w).sum() / w.sum():.1f}."]
    text = "\n".join(lines) + "\n"
    print(text)
    path = ROOT / "reports" / "all_names_summary.txt"
    path.write_text(text, encoding="utf-8")
    print(f"numbers: {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main([n.upper() for n in sys.argv[1:]] or ORDER)
