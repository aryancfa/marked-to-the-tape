"""
reference.py — reads the dividend schedule kept under reference/.

Each row is one ex-dividend date with its cash amount and a status:
  declared   announced by the company
  projected  our own assumption, because nothing has been announced yet

The status travels with the numbers so that any result leaning on a
projected dividend can say so (hard rule 6: label what the model assumed).

An optional `announced` column gives the date the company declared the
dividend. On a valuation day before that date nobody knew the amount or the
date. `as_known_on` returns the schedule as it could have been written that
day: dividends not yet announced are replaced by what the projection rule
gives from the ones that were known. Empty means announced before the option
data starts. Where the announcement date could not be found the ex-date
itself is entered, so the dividend counts as unknown until it goes ex.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import NamedTuple, Sequence

import pandas as pd

from tradingcal import TSX, trading_days

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


# Names whose payout varies with results or holdings: the next one is taken to
# repeat the one a year earlier. Every other name gets the raise rule.
REPEAT_RULE = frozenset({"XIU", "ZEB", "XEG", "ABX"})


def rule_for(underlying: str) -> str:
    return "repeat" if underlying.upper() in REPEAT_RULE else "raise"


def a_year_on(d: date, venue: str = TSX) -> date:
    """The same date a year later, moved back to a trading day if it is not one."""
    try:
        target = d.replace(year=d.year + 1)
    except ValueError:                              # 29 February
        target = d.replace(year=d.year + 1, day=28)
    return trading_days(venue, target - timedelta(days=7), target)[-1]


def project(known: Sequence[tuple[date, float]], rule: str,
            horizon: date) -> list[tuple[date, float]] | None:
    """
    Dividends after the last known one, out to `horizon`, from the known ones
    alone. No option price and no later information goes in.

      raise   same date as a year earlier; the previous amount plus whatever
              the raise was at the same point a year earlier
      repeat  same date and amount as a year earlier

    None if there is under a year of history plus one payment, which is what
    the raise rule needs to see last year's raise.
    """
    if rule not in ("raise", "repeat"):
        raise ValueError(f"unknown dividend rule {rule!r}")
    series = sorted(known)
    if not series:
        return None
    # payments a year: how many fell in the 350 days up to the latest one
    freq = sum(1 for d, _ in series if (series[-1][0] - d).days < 350)
    if len(series) <= freq:
        return None
    out = []
    while True:
        src_date, src_cash = series[-freq]          # the counterpart a year back
        nxt = a_year_on(src_date)
        if nxt > horizon or nxt <= series[-1][0]:
            break
        cash = src_cash if rule == "repeat" else round(
            series[-1][1] + (src_cash - series[-freq - 1][1]), 6)
        series.append((nxt, cash))
        out.append((nxt, cash))
    return out


def as_known_on(divs: Sequence[Dividend], day: date, rule: str = "raise") -> list[Dividend]:
    """
    The schedule as it could have been written on `day`: point-in-time.

    A dividend announced after `day` was not known on `day`, in amount or in
    date. Pricing that day with the amount declared later is hindsight, even
    if the row is labelled. So every dividend not yet announced on `day` is
    replaced by what the rule projects from the dividends that WERE known
    (project). Rows already known are returned as they are.

    One thing is kept from the file: where it holds a hand-entered projected
    row within 15 days of a projected date, that row's DATE is used. Those
    dates are either the rule's own or come from a schedule the issuer
    publishes in advance, which was public on `day` too. The amount is always
    the rule's.

    With too little history to project from, the file is returned unchanged
    (which is hindsight); `has_history` says whether that happened.
    """
    known = [x for x in divs if x.status == "declared"
             and (x.announced is None or x.announced <= day)]
    if len(known) == len(divs) or not divs:
        return list(divs)
    # a month past the file's last row: a projected date can fall a few days
    # after the date that was later declared
    horizon = max(x.ex_date for x in divs) + timedelta(days=31)
    chain = project([(x.ex_date, x.cash) for x in known], rule, horizon)
    if chain is None:
        return list(divs)
    hand = [x for x in divs if x.status == "projected"]
    out = list(known)
    for d, cash in chain:
        near = [x.ex_date for x in hand if abs((x.ex_date - d).days) <= 15]
        out.append(Dividend(near[0] if near else d, cash, "projected", None))
    return sorted(out, key=lambda x: x.ex_date)


def has_history(divs: Sequence[Dividend], day: date, rule: str = "raise") -> bool:
    """True if as_known_on can project from what was known on `day`."""
    known = [(x.ex_date, x.cash) for x in divs if x.status == "declared"
             and (x.announced is None or x.announced <= day)]
    if len(known) == len(divs) or not known:
        return len(known) == len(divs)
    one_year_out = max(d for d, _ in known) + timedelta(days=400)
    return project(known, rule, one_year_out) is not None


def first_projected(divs: list[Dividend], valuation: date) -> date | None:
    """Ex-date of the first dividend after `valuation` that was not known on
    that day: projected, or declared only later. None if there is none.
    Every expiry on or after it rests on an assumed dividend."""
    return min((x.ex_date for x in divs if x.ex_date > valuation
                and (x.status == "projected"
                     or (x.announced is not None and x.announced > valuation))),
               default=None)
