"""
dividend_three_way.py — three independent views of next year's dividends (A4, B5).

Needs scripts/wrds_ibes.py (analysts' consensus) and scripts/run_days.py for
each name (the option-implied view).

    .venv/Scripts/python scripts/dividend_three_way.py

A dividend that has not been declared has to come from somewhere. This puts
three sources side by side for each name's coming fiscal year:

  rule       this project's schedule: declared amounts where they exist, and
             otherwise last year's dates with a raise the size of last year's
  options    the same schedule with each not-yet-known dividend replaced by
             the amount the options imply (the step in forward error across
             its ex-date; see scripts/funding_split.py), where one could be
             isolated
  analysts   the I/B/E/S consensus dividend per share for the fiscal year

They share nothing: the rule uses only past dividends, the options use only
option prices, the analysts use neither. Where two of them agree against the
third, the third is the one to doubt.

How a dividend is assigned to a fiscal year: by its ex-date falling in the
twelve months up to the fiscal year-end. For these names that matches how the
company counts them (checked on the banks' quarters against the quarterly
consensus). The consensus is the latest on file, which may be weeks older
than the option data, and its date is printed.

The model's inputs are NOT changed by this. It is a comparison only.

Prints DERIVED numbers and writes reports/dividend_three_way.txt. Consensus
figures are summarised per name and year only; no I/B/E/S rows are written.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
import pandas as pd

from funding_split import implied_steps, load
from loaders.reference import load_dividends

NAMES = ["RY", "TD", "BNS", "BMO", "MFC", "CNQ", "SU", "ENB", "CNR", "BCE"]
IBES = ROOT / "data/raw/wrds/ibes_dps.csv"


def consensus(name: str, raw: pd.DataFrame) -> pd.DataFrame:
    """Latest annual consensus rows for one name: the Canadian-dollar series
    under its own exchange ticker with the most history."""
    x = raw[(raw["oftic"] == name) & (raw["curcode"] == "CAD") & (raw["fiscalp"] == "ANN")
            & raw["source_table"].str.endswith("xepsint")]
    if x.empty:
        return x
    x = x[x["ticker"] == x["ticker"].value_counts().index[0]]
    return x[x["statpers"] == x["statpers"].max()].sort_values("fpedats")


def main() -> None:
    if not IBES.exists():
        raise SystemExit("no consensus on disk; run scripts/wrds_ibes.py first")
    raw = pd.read_csv(IBES, parse_dates=["statpers", "fpedats"])
    rows = []
    for name in NAMES:
        cons = consensus(name, raw)
        try:
            results = load(name)
        except SystemExit:
            results = []
        schedule = load_dividends(ROOT / "reference" / f"{name.lower()}_dividends.csv")
        steps = implied_steps(results, schedule) if results else pd.DataFrame()
        implied = (steps.groupby("ex_date")["implied"].mean()[
            steps.groupby("ex_date")["implied"].size() >= 20] if len(steps) else pd.Series(dtype=float))
        for c in cons.itertuples():
            end: date = c.fpedats.date()
            start = end.replace(year=end.year - 1)
            in_year = [x for x in schedule if start < x.ex_date <= end]
            if len(in_year) < 4 or c.numest < 3:
                continue                            # a part year, or too few analysts
            rule = sum(x.cash for x in in_year)
            swapped = [x for x in in_year if x.ex_date in implied.index]
            opt = sum(float(implied[x.ex_date]) if x.ex_date in implied.index else x.cash
                      for x in in_year)
            rows.append({
                "name": name, "fiscal year to": end,
                "projected of 4": sum(x.status == "projected" for x in in_year),
                "rule": rule, "options": opt if swapped else np.nan,
                "implied of 4": len(swapped),
                "analysts": c.meanest, "analysts sd": c.stdev, "n": int(c.numest),
                "as of": c.statpers.date(),
                "rule vs analysts %": (rule / c.meanest - 1.0) * 100.0,
                "options vs analysts %": ((opt / c.meanest - 1.0) * 100.0) if swapped else np.nan})
    t = pd.DataFrame(rows)
    fwd = t[t["projected of 4"] > 0]
    lines = ["Derived statistics only: dividends per share summed over a fiscal year.", "",
             "Three views of each fiscal year's dividends per share, in Canadian dollars.",
             "rule = this project's schedule; options = the same with the dividends the options",
             "imply swapped in where one could be isolated ('implied of 4' says how many);",
             "analysts = I/B/E/S consensus mean, with the spread across analysts and their number.",
             "", t.round(3).to_string(index=False), ""]
    if len(fwd):
        lines += [
            "Years with at least one projected dividend:",
            f"  rule against analysts: mean {fwd['rule vs analysts %'].mean():+.1f}%, mean "
            f"absolute {fwd['rule vs analysts %'].abs().mean():.1f}%, {len(fwd)} name-years.",
        ]
        both = fwd.dropna(subset=["options vs analysts %"])
        if len(both):
            lines += [
                f"  options against analysts: mean {both['options vs analysts %'].mean():+.1f}%, "
                f"mean absolute {both['options vs analysts %'].abs().mean():.1f}%, "
                f"{len(both)} name-years.",
                f"  on those same {len(both)}, rule against analysts: mean absolute "
                f"{both['rule vs analysts %'].abs().mean():.1f}%."]
    text = "\n".join(lines) + "\n"
    print(text)
    path = ROOT / "reports" / "dividend_three_way.txt"
    path.write_text(text, encoding="utf-8")
    print(f"numbers: {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
