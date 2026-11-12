"""
compustat.py — daily returns from the Compustat files scripts/wrds_prices.py
downloads (build sheet C4; docs/work-list.md section 2).

A hedge is judged on what the holder actually earns, so a share's return here
includes its dividend: on the ex-date the price falls by about the dividend
and the holder receives it in cash, and counting only the price would record
a loss that never happened. Splits are removed with the file's adjustment
factor for the same reason.

The data is licensed and stays under data/raw/wrds/. Tests use made-up files.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from tradingcal import TSX, trading_days


def _clean(raw: pd.DataFrame, today: date | None) -> pd.DataFrame:
    raw = raw.copy()
    raw["datadate"] = pd.to_datetime(raw["datadate"])
    raw["prccd"] = pd.to_numeric(raw["prccd"], errors="coerce")
    limit = pd.Timestamp(today or date.today())
    # some series carry placeholder rows dated in the future, and a few rows
    # have no price; neither is a trading day
    raw = raw[(raw["datadate"] <= limit) & (raw["prccd"] > 0)]
    return raw.sort_values("datadate").drop_duplicates("datadate").set_index("datadate")


def _one_session(r: pd.Series, venue: str) -> pd.Series:
    """
    Keep only returns earned over exactly ONE trading session.

    A return is labelled by the day it ends on. If the row before it is
    missing, the return covers two sessions or more, and joined by end date
    to another series it would be set against that series' one-day move: a
    share's two-day 21% beside the index's one-day 10%. So a return whose
    previous row is not the previous session of `venue` is dropped, as is one
    dated on a day the venue was closed. How many were dropped is kept in
    `.attrs["dropped"]`, with their dates in `.attrs["dropped_dates"]`.
    """
    if r.empty:
        r.attrs.update(dropped=0, dropped_dates=[])
        return r
    days = [d.date() for d in r.index]
    sessions = trading_days(venue, days[0], days[-1])
    pos = {d: i for i, d in enumerate(sessions)}
    ok = [d in pos and p in pos and pos[d] - pos[p] == 1
          for p, d in zip(days, days[1:])]
    keep = pd.Series([False] + ok, index=r.index)       # the first row has no return
    out = r[keep & r.notna()]
    out.attrs.update(dropped=int((~keep).sum() - 1),
                     dropped_dates=[d for d, k in zip(days[1:], ok) if not k])
    return out


def load_total_returns(path: str | Path, today: date | None = None,
                       venue: str = TSX) -> pd.Series:
    """
    Daily total return of one listing: price change plus any cash dividend
    going ex that day, with splits taken out.

        return = (close + dividend) / adjustment, over the previous close /
                 its adjustment, minus one

    Only one-session returns are kept (_one_session): a day whose previous
    row is missing is dropped, not given a return over the gap.
    """
    d = _clean(pd.read_csv(path, low_memory=False), today)
    adj = pd.to_numeric(d["ajexdi"], errors="coerce").fillna(1.0)
    div = pd.to_numeric(d["div"], errors="coerce").fillna(0.0)
    price = d["prccd"] / adj
    r = (price + div / adj) / price.shift(1) - 1.0
    return _one_session(r.rename(Path(path).stem.replace("prices_", "")), venue)


def load_index_returns(path: str | Path, today: date | None = None,
                       venue: str = TSX) -> pd.Series:
    """Daily price return of an index level. A future on the index moves with
    the price index, not the total-return one, so this is the right series to
    stand in for the future. Only one-session returns are kept."""
    d = _clean(pd.read_csv(path), today)
    return _one_session((d["prccd"] / d["prccd"].shift(1) - 1.0).rename("index"), venue)
