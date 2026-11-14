"""
episodes.py — real dated market moves, to replay today's book through (C3).

A made-up shock is a number someone typed. A replay takes what the market
actually did over a named stretch, every name's move on every day together,
and applies it to today's book: "if February to March 2020 started tomorrow".
Names move as they really moved relative to each other, so no beta and no
correlation assumption stands between the history and the result.

The moves are TOTAL returns (price plus dividends) compounded from the
episode's first close. The replay lowers today's share price by the
dividends due inside the window before applying them (src/horizon.py), so a
total return is the right measure: it is the move relative to the expected,
ex-dividend price. The index future moves with the index's price return,
because a future does not receive dividends.

A name with no return on some session of an episode is left out of that
episode, and the caller says so: compounding past a missing day would drop
that day's move without saying so.

Nothing here reads a file.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

from tradingcal import trading_days


@dataclass(frozen=True)
class Episode:
    key: str
    title: str
    start: date          # the close the moves are measured from
    end: date
    why: str


EPISODES = (
    Episode("jan2015", "January 2015: oil collapse and a surprise rate cut",
            date(2014, 12, 31), date(2015, 1, 30),
            "Crude fell below $45; the Bank of Canada cut on 21 January without warning."),
    Episode("covid2020", "February to March 2020: the pandemic crash",
            date(2020, 2, 20), date(2020, 3, 23),
            "The TSX fell about 37% from its 20 February high to the 23 March low."),
    Episode("wti2020", "April 2020: negative WTI",
            date(2020, 4, 14), date(2020, 4, 22),
            "The May WTI future settled at minus $37.63 on 20 April."),
    Episode("boc2022", "2022 to 2023: the Bank of Canada's hiking cycle",
            date(2022, 3, 1), date(2023, 7, 12),
            "Ten hikes from 0.25% to 5.00%, 2 March 2022 to 12 July 2023."),
)


def sessions(ep: Episode, venue: str = "TSX") -> list[date]:
    """The episode's trading sessions after its first close, to its end."""
    return [d for d in trading_days(venue, ep.start, ep.end) if d > ep.start]


def path(returns: pd.DataFrame, ep: Episode, venue: str = "TSX") -> tuple[pd.DataFrame, dict]:
    """Cumulative move of every column from the episode's first close, one
    row per session. Returns (moves, missing), where missing maps each column
    left out to the sessions it has no return on."""
    days = sessions(ep, venue)
    if not days:
        raise ValueError(f"{ep.key}: no sessions between {ep.start} and {ep.end}")
    r = returns.copy()
    r.index = [d.date() if hasattr(d, "date") else d for d in r.index]
    r = r.reindex(days)
    missing = {c: [d for d in days if pd.isna(r.at[d, c])] for c in r.columns}
    missing = {c: v for c, v in missing.items() if v}
    keep = [c for c in r.columns if c not in missing]
    moves = (1.0 + r[keep]).cumprod() - 1.0
    return moves, missing
