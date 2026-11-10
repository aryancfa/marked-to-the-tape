"""
reference.py — reads the dividend schedule kept under data/reference/.

Each row is one ex-dividend date with its cash amount and a status:
  declared   announced by the company
  projected  our own assumption, because nothing has been announced yet

The status travels with the numbers so that any result leaning on a
projected dividend can say so (hard rule 6: label what the model assumed).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

STATUSES = ("declared", "projected")

Dividend = tuple[date, float, str]      # (ex-date, cash per share, status)


def load_dividends(path: str | Path) -> list[Dividend]:
    """Rows of the schedule in date order. An unknown status raises."""
    df = pd.read_csv(path, parse_dates=["ex_date"]).sort_values("ex_date")
    bad = sorted(set(df["status"]) - set(STATUSES))
    if bad:
        raise ValueError(f"{path}: unknown dividend status {bad}; "
                         f"expected one of {STATUSES}")
    return [(d.date(), float(c), str(s))
            for d, c, s in zip(df["ex_date"], df["cash"], df["status"])]


def first_projected(divs: list[Dividend], valuation: date) -> date | None:
    """Ex-date of the first projected dividend after `valuation`, or None.
    Every expiry on or after it rests on an assumed dividend."""
    return min((d for d, _, s in divs if s == "projected" and d > valuation),
               default=None)
