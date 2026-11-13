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

Some issuers publish next year's ex-DATES in advance and only the amounts a
week ahead (BlackRock's fund distribution schedule). Those dates are kept in
reference/published_dates.csv with the day they became public. Such a date
is a fact from that day on, though the amount is still a projection:
`date_known` carries it, and `uncertain_ex_dates` leaves it out.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import NamedTuple, Sequence

import pandas as pd

from tradingcal import TSX, trading_days

STATUSES = ("declared", "projected")
CURRENCIES = ("CAD", "USD")

class Dividend(NamedTuple):
    ex_date: date
    cash: float                     # per share
    status: str                     # "declared" or "projected"
    announced: date | None = None   # None: known before the data starts
    date_known: date | None = None  # the ex-DATE was published by the issuer on this day
    currency: str = "CAD"           # the currency the dividend is DECLARED in


PUBLISHED = "published_dates.csv"


def published_dates(folder: str | Path, underlying: str) -> dict[date, date]:
    """ex-date -> the day the issuer made that date public, for one name.
    Empty if the folder has no published_dates.csv or the name is not in it."""
    p = Path(folder) / PUBLISHED
    if not p.exists():
        return {}
    df = pd.read_csv(p, parse_dates=["ex_date", "known_from"])
    df = df[df["underlying"].str.upper() == underlying.upper()]
    return {d.date(): k.date() for d, k in zip(df["ex_date"], df["known_from"])}


def load_dividends(path: str | Path) -> list[Dividend]:
    """Rows of the schedule in date order. An unknown status raises."""
    df = pd.read_csv(path, parse_dates=["ex_date"]).sort_values("ex_date")
    bad = sorted(set(df["status"]) - set(STATUSES))
    if bad:
        raise ValueError(f"{path}: unknown dividend status {bad}; "
                         f"expected one of {STATUSES}")
    ann = (pd.to_datetime(df["announced"]) if "announced" in df.columns
           else pd.Series(pd.NaT, index=df.index))
    name = Path(path).stem.replace("_dividends", "")
    pub = published_dates(Path(path).parent, name)
    missing = sorted(set(pub) - {d.date() for d in df["ex_date"]})
    if missing:
        raise ValueError(f"{path}: published ex-dates with no row in the schedule: {missing}")
    cur = (df["currency"].fillna("CAD").str.upper() if "currency" in df.columns
           else pd.Series("CAD", index=df.index))
    odd = sorted(set(cur) - set(CURRENCIES))
    if odd:
        raise ValueError(f"{path}: unknown currency {odd}; expected one of {CURRENCIES}")
    return [Dividend(d.date(), float(c), str(s), None if pd.isna(a) else a.date(),
                     pub.get(d.date()), str(k))
            for d, c, s, a, k in zip(df["ex_date"], df["cash"], df["status"], ann, cur)]


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


def in_cad(divs: Sequence[Dividend], day: date, fx=None) -> list[Dividend]:
    """Every dividend in Canadian dollars, as valued on `day`.

    A dividend declared in US dollars takes the share price down by the
    amount times the exchange rate on its ex-date. Seen from `day` that rate
    is not known; the rate on `day` stands in for it (see src/fx.py), so the
    amount is re-converted every valuation day. `fx(day)` gives Canadian
    dollars per US dollar. With a US-dollar row and no `fx` this raises: an
    amount in the wrong currency is never passed on as if it were right."""
    if all(x.currency == "CAD" for x in divs):
        return list(divs)
    if fx is None:
        raise ValueError("the schedule has US-dollar dividends and no exchange rate was given")
    rate = float(fx(day))
    return [x if x.currency == "CAD" else x._replace(cash=round(x.cash * rate, 6),
                                                     currency="CAD") for x in divs]


def as_known_on(divs: Sequence[Dividend], day: date, rule: str = "raise",
                fx=None) -> list[Dividend]:
    """Point-in-time schedule in Canadian dollars: _as_known_on, then in_cad.
    The projection rule runs in the currency the dividends are declared in,
    so a US-dollar payer repeats last year's US-dollar amount, and only then
    is everything converted at `day`'s rate."""
    return in_cad(_as_known_on(divs, day, rule), day, fx)


def _as_known_on(divs: Sequence[Dividend], day: date, rule: str = "raise") -> list[Dividend]:
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

    A row whose ex-date the issuer had published by `day` (date_known) gives
    its date to the projection near it, declared later or not, and the
    projected row carries date_known: its amount is a guess, its date is not.

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
    firm = [x for x in divs if x.date_known is not None and x.date_known <= day]
    money = known[-1].currency if known else "CAD"      # projections follow the declared ones
    out = list(known)
    for d, cash in chain:
        pub = [x for x in firm if abs((x.ex_date - d).days) <= 15]
        if pub:
            out.append(Dividend(pub[0].ex_date, cash, "projected", None, pub[0].date_known,
                                money))
            continue
        near = [x.ex_date for x in hand if abs((x.ex_date - d).days) <= 15]
        out.append(Dividend(near[0] if near else d, cash, "projected", None, None, money))
    return sorted(out, key=lambda x: x.ex_date)


def uncertain_ex_dates(divs: Sequence[Dividend], day: date) -> list[date]:
    """Future ex-dates whose DATE is a guess on `day`: projected rows of a
    point-in-time schedule (as_known_on), less those the issuer had already
    published. An expiry beside one of these may hold a dividend the model
    left outside it, or the reverse (surface.date_uncertain)."""
    return [x.ex_date for x in divs if x.ex_date > day and x.status == "projected"
            and not (x.date_known is not None and x.date_known <= day)]


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
