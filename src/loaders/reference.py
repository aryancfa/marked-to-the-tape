"""
reference.py — reads the dividend schedule kept under data/reference/.

Each row is one ex-dividend date with its cash amount and a status:
  declared   announced by the company
  projected  our own assumption, because nothing has been announced yet

The status travels with the numbers so that any result leaning on a
projected dividend can say so (hard rule 6: label what the model assumed).

An optional `announced` column gives the date the company declared the
dividend. On a valuation day before that date nobody knew the amount, so the
row counts as projected for that day even though the file now says declared.
The amount used is still the one declared later, which is hindsight; the
label is what keeps such expiries out of anything fitted from "declared"
dividends.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import NamedTuple

import pandas as pd

STATUSES = ("declared", "projected")

class Dividend(NamedTuple):
    ex_date: date
    cash: float                     # per share
    status: str                     # "declared" or "projected"
    announced: date | None = None   # None: known before the data starts


def load_dividends(path: str | Path) -> list[Dividend]:
    """Rows of the schedule in date order. An unknown status raises."""
    df = pd.read_csv(path, parse_dates=["ex_date"]).sort_values("ex_date")
    bad = sorted(set(df["status"]) - set(STATUSES))
    if bad:
        raise ValueError(f"{path}: unknown dividend status {bad}; "
                         f"expected one of {STATUSES}")
    ann = (pd.to_datetime(df["announced"]) if "announced" in df.columns
           else pd.Series(pd.NaT, index=df.index))
    return [Dividend(d.date(), float(c), str(s), None if pd.isna(a) else a.date())
            for d, c, s, a in zip(df["ex_date"], df["cash"], df["status"], ann)]


def first_projected(divs: list[Dividend], valuation: date) -> date | None:
    """Ex-date of the first dividend after `valuation` that was not known on
    that day: projected, or declared only later. None if there is none.
    Every expiry on or after it rests on an assumed dividend."""
    return min((x.ex_date for x in divs if x.ex_date > valuation
                and (x.status == "projected"
                     or (x.announced is not None and x.announced > valuation))),
               default=None)
