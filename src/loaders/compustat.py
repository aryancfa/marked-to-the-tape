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


def _clean(raw: pd.DataFrame, today: date | None) -> pd.DataFrame:
    raw = raw.copy()
    raw["datadate"] = pd.to_datetime(raw["datadate"])
    raw["prccd"] = pd.to_numeric(raw["prccd"], errors="coerce")
    limit = pd.Timestamp(today or date.today())
    # some series carry placeholder rows dated in the future, and a few rows
    # have no price; neither is a trading day
    raw = raw[(raw["datadate"] <= limit) & (raw["prccd"] > 0)]
    return raw.sort_values("datadate").drop_duplicates("datadate").set_index("datadate")


def load_total_returns(path: str | Path, today: date | None = None) -> pd.Series:
    """
    Daily total return of one listing: price change plus any cash dividend
    going ex that day, with splits taken out.

        return = (close + dividend) / adjustment, over the previous close /
                 its adjustment, minus one

    A day whose previous row is missing takes its return over the gap.
    """
    d = _clean(pd.read_csv(path, low_memory=False), today)
    adj = pd.to_numeric(d["ajexdi"], errors="coerce").fillna(1.0)
    div = pd.to_numeric(d["div"], errors="coerce").fillna(0.0)
    price = d["prccd"] / adj
    r = (price + div / adj) / price.shift(1) - 1.0
    return r.dropna().rename(Path(path).stem.replace("prices_", ""))


def load_index_returns(path: str | Path, today: date | None = None) -> pd.Series:
    """Daily price return of an index level. A future on the index moves with
    the price index, not the total-return one, so this is the right series to
    stand in for the future."""
    d = _clean(pd.read_csv(path), today)
    return (d["prccd"] / d["prccd"].shift(1) - 1.0).dropna().rename("index")
