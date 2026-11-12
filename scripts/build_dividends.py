"""
build_dividends.py — dividend schedules for the names beyond RY and XIU (A4).

    .venv/Scripts/python scripts/build_dividends.py            # all names below
    .venv/Scripts/python scripts/build_dividends.py TD BNS     # some

Writes reference/<name>_dividends.csv in the layout loaders/reference.py reads.
It never touches the RY or XIU files, which are curated by hand.

What goes in
------------
Declared rows: every cash dividend Compustat's daily file shows on its
ex-date, from a year before the option files start to the latest row. These
are public facts (ex-date and amount per share on the Toronto listing).

Announcement dates, and dividends declared but not yet paid: Compustat has
neither (a dividend appears there only on its ex-date). Both come from
reference/dividend_announcements.csv, which is kept by hand from company
releases. A row there with no cash sets `announced` on the matching ex-date;
a row with cash adds a declared dividend that is still to come, and the
projections then run on from it. On a valuation day before `announced` the
reader counts the dividend as projected for that day (loaders/reference.py).
A declared row whose announcement fell before the option files start needs
no date and has none.

Projected rows, to the end of next year, by one of two rules that use no
option prices:

  raise   (companies with a regular dividend) same dates as a year earlier;
          the amount is the previous one plus whatever the raise was at the
          same point a year earlier. This is the rule already used for RY.
          The robustness table (B10) found the market prices about half of
          such a raise on RY, so treat these as an input under test.
  repeat  (funds, and ABX, whose payout varies with results) same date and
          amount as a year earlier.

A projected date that is not a trading day moves to the trading day before.
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import pandas as pd

from tradingcal import TSX, trading_days

RULES = {"TD": "raise", "BNS": "raise", "BMO": "raise", "CNQ": "raise", "SU": "raise",
         "MFC": "raise", "ENB": "raise", "CNR": "raise", "BCE": "raise",
         "ABX": "repeat", "ZEB": "repeat", "XEG": "repeat", "SHOP": "none"}
FIRST = date(2025, 1, 1)            # well over a year before the option files start
DATA_START = date(2026, 5, 8)       # first day of the exchange option files
HORIZON = date(2027, 12, 31)        # the longest expiry used is about 400 days out
READ = "2026-11-11"


def a_year_on(d: date) -> date:
    try:
        target = d.replace(year=d.year + 1)
    except ValueError:                              # 29 February
        target = d.replace(year=d.year + 1, day=28)
    return trading_days(TSX, target - timedelta(days=7), target)[-1]


def build(name: str) -> pd.DataFrame:
    rule = RULES[name]
    cols = ["underlying", "ex_date", "cash", "status", "announced", "source"]
    if rule == "none":
        return pd.DataFrame(columns=cols)
    raw = pd.read_csv(ROOT / "data/raw/wrds" / f"prices_{name}.csv", parse_dates=["datadate"])
    raw["div"] = pd.to_numeric(raw["div"], errors="coerce")
    today = pd.Timestamp(READ)
    paid = raw[(raw["datadate"] >= pd.Timestamp(FIRST)) & (raw["datadate"] <= today)
               & (raw["div"] > 0)]
    known = [(d.date(), float(c)) for d, c in zip(paid["datadate"], paid["div"])]
    ann = pd.read_csv(ROOT / "reference" / "dividend_announcements.csv",
                      parse_dates=["ex_date", "announced"])
    ann = ann[ann["underlying"] == name]
    told = {r.ex_date.date(): r for r in ann.itertuples()}
    rows = []
    for d, c in known:
        a = told.pop(d, None)
        if a is None and d >= DATA_START:
            note = (" No announcement date found: on days before it was announced the "
                    "amount is hindsight")
        else:
            note = "" if a is None else f" Announced: {a.source}"
        rows.append(dict(underlying=name, ex_date=d, cash=c, status="declared",
                         announced="" if a is None else a.announced.date(),
                         source=f"Compustat daily file (cash dividend on its ex-date); "
                                f"read {READ}.{note}"))
    for d, a in sorted(told.items()):               # declared, ex-date still to come
        if pd.isna(a.cash) or d <= known[-1][0]:
            raise SystemExit(f"{name}: announcement for {d} matches no dividend on file")
        known.append((d, float(a.cash)))
        rows.append(dict(underlying=name, ex_date=d, cash=float(a.cash), status="declared",
                         announced=a.announced.date(), source=a.source))
    series = list(known)                            # grows as projections are added
    # payments a year: how many fell in the 350 days up to the latest one
    freq = sum(1 for d, _ in known if (known[-1][0] - d).days < 350)
    if len(known) <= freq:
        raise SystemExit(f"{name}: under two years of dividends on file; cannot project")
    while True:
        # the next payment's counterpart is the one `freq` payments back
        src_date, src_cash = series[-freq]
        nxt = a_year_on(src_date)
        if nxt > HORIZON:
            break
        if nxt <= series[-1][0]:
            raise SystemExit(f"{name}: projected {nxt} is not after {series[-1][0]}")
        if rule == "repeat":
            cash, why = src_cash, "RULE: same date and amount as a year earlier"
        else:
            raise_then = round(src_cash - series[-freq - 1][1], 6)
            cash = round(series[-1][1] + raise_then, 6)
            why = ("RULE: same date as a year earlier; previous amount plus the raise at the "
                   f"same point a year earlier ({raise_then:+g})")
        series.append((nxt, cash))
        rows.append(dict(underlying=name, ex_date=nxt, cash=cash, status="projected",
                         announced="", source=why))
    return pd.DataFrame(rows, columns=cols)


def main(names: list[str]) -> None:
    for name in names:
        if name in ("RY", "XIU"):
            raise SystemExit(f"{name}'s schedule is curated by hand; not rebuilt")
        out = build(name)
        path = ROOT / "reference" / f"{name.lower()}_dividends.csv"
        out.to_csv(path, index=False, lineterminator="\n")
        proj = out[out["status"] == "projected"]
        print(f"{name:5s} {RULES[name]:6s} declared {int((out['status'] == 'declared').sum()):2d}, "
              f"projected {len(proj):2d}: "
              + ", ".join(f"{d} {c:g}" for d, c in zip(proj["ex_date"], proj["cash"])))


if __name__ == "__main__":
    main([n.upper() for n in sys.argv[1:]] or list(RULES))
