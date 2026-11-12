"""
loaders/ibkr.py — closing quotes captured through the Interactive Brokers API
(build sheet SRC), read into the SAME tables the exchange-file loader returns.

The exchange's website may not be scripted (hard rule 2), so history from it
is downloaded by hand. Going forward, scripts/capture_ibkr.py takes a snapshot
of each chain after the close and saves it under data/raw/ibkr/, which is
gitignored like every other raw file. This module holds everything about that
capture that can be tested without a connection:

  choose_contracts   which expiries and strikes to ask for
  CAPTURE_COLUMNS    the layout the capture script writes
  load_ibkr_options / load_ibkr_underlying
                     the same columns as loaders.mx, so nothing downstream
                     (surface.analyse_day, the front chart) has to change
  capture_summary / missed_days
                     the capture log: what was asked for, what came back,
                     and which trading days have no capture at all

What a capture is NOT, compared with the exchange's file
--------------------------------------------------------
  * No settlement price. A row with no two-sided quote has nothing to be
    marked from and is tiered "no_price"; it is counted, never guessed.
  * No exchange implied vol, so the third reference on the front chart
    (ours minus the exchange's) is empty on captured days.
  * No open interest in a snapshot. The column is there and empty.
  * It is a snapshot at the moment of capture, not an official close. The
    time is recorded on every row.
  * Interactive Brokers keeps nothing for expired options, so this builds
    history forward only.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np
import pandas as pd

from loaders.mx import (NO_PRICE, OPTION_COLUMNS, QUOTED, TRADED,
                        UNDERLYING_COLUMNS)
from tradingcal import TSX, trading_days

CAPTURE_COLUMNS = [
    "captured_at",      # local time of the snapshot, ISO format
    "date",             # the trading day it belongs to
    "underlying", "expiry", "strike", "call_put",
    "bid", "ask", "last", "volume",
    "und_bid", "und_ask", "und_last",       # the share, taken in the same pass
]
LOG_COLUMNS = ["date", "underlying", "asked", "returned", "two_sided", "missed",
               "seconds", "note"]


def choose_contracts(spot: float, expiries: Iterable[date], strikes: Iterable[float],
                     day: date, moneyness: float = 0.12, min_days: int = 1,
                     max_days: int = 400) -> list[tuple[date, float, str]]:
    """
    (expiry, strike, "C" or "P") for every option worth asking for: expiries
    from `min_days` to `max_days` ahead and strikes within `moneyness` of the
    share price. The same window surface.near_the_money keeps, so nothing is
    requested that the chart would throw away. Market data requests are
    rationed, and a full chain is several times this size.
    """
    if not spot > 0:
        raise ValueError("a share price is needed to choose strikes")
    lo, hi = spot * (1.0 - moneyness), spot * (1.0 + moneyness)
    keep_e = sorted(e for e in set(expiries) if min_days <= (e - day).days <= max_days)
    keep_k = sorted(float(k) for k in set(strikes) if lo <= float(k) <= hi)
    return [(e, k, cp) for e in keep_e for k in keep_k for cp in "CP"]


def _paths(source) -> list[Path]:
    if isinstance(source, (str, Path)):
        p = Path(source)
        found = sorted(p.rglob("quote_*.csv")) if p.is_dir() else [p]
    else:
        found = [Path(s) for s in source]
    if not found:
        raise FileNotFoundError(f"no capture files found in {source!r}")
    return found


def _read(source) -> pd.DataFrame:
    frames = []
    for path in _paths(source):
        df = pd.read_csv(path)
        missing = [c for c in CAPTURE_COLUMNS if c not in df.columns]
        if missing:
            raise ValueError(f"{path.name}: missing columns {missing}")
        frames.append(df[CAPTURE_COLUMNS])
    raw = pd.concat(frames, ignore_index=True)
    raw["date"] = pd.to_datetime(raw["date"], format="%Y-%m-%d")
    raw["expiry"] = pd.to_datetime(raw["expiry"], format="%Y-%m-%d")
    bad = sorted(set(raw["call_put"].unique()) - {"C", "P"})
    if bad:
        raise ValueError(f"unknown call_put values {bad}")
    # Two captures of the same series on the same day are not something to
    # choose between silently: keep the files apart or delete one.
    clash = raw.duplicated(["date", "underlying", "expiry", "strike", "call_put"], keep=False)
    if clash.any():
        raise ValueError(f"{int(clash.sum())} rows repeat a series on the same day; "
                         "resolve the capture files before loading")
    # the API reports "no value" as -1 or as an empty field
    for c in ("bid", "ask", "last", "volume", "und_bid", "und_ask", "und_last"):
        raw[c] = pd.to_numeric(raw[c], errors="coerce")
        raw[c] = raw[c].where(raw[c] > 0)
    return raw


def load_ibkr_options(source) -> pd.DataFrame:
    """
    One row per option series per day with loaders.mx.OPTION_COLUMNS, from a
    capture file, a folder of them (searched recursively) or a list.

    settle, open_interest and mx_iv are empty: a snapshot has none of them.
    A row is "traded" if it shows volume, "quoted" if it has a two-sided
    quote, and "no_price" otherwise. The mark is the quote midpoint where a
    two-sided quote exists and nothing else: a last trade of unknown age is
    not used as a price.
    """
    raw = _read(source)
    opt = pd.DataFrame({
        "date": raw["date"], "underlying": raw["underlying"].astype(str),
        "expiry": raw["expiry"], "strike": raw["strike"].astype(float),
        "call_put": raw["call_put"],
        "bid": raw["bid"].fillna(0.0), "ask": raw["ask"].fillna(0.0),
        "settle": np.nan,
        "volume": raw["volume"].fillna(0).astype(int),
        "open_interest": 0,
    })
    opt["traded"] = opt["volume"] > 0
    opt["two_sided"] = (opt["bid"] > 0) & (opt["ask"] >= opt["bid"])
    opt["tier"] = np.select([opt["traded"] & opt["two_sided"], opt["two_sided"]],
                            [TRADED, QUOTED], default=NO_PRICE)
    opt["mark"] = np.where(opt["two_sided"], 0.5 * (opt["bid"] + opt["ask"]), np.nan)
    opt["mark_source"] = np.where(opt["two_sided"], "mid", "none")
    opt["settle_in_quote"] = None
    opt["mx_iv"] = np.nan
    return opt[OPTION_COLUMNS].sort_values(
        ["date", "underlying", "expiry", "strike", "call_put"]).reset_index(drop=True)


def load_ibkr_underlying(source) -> pd.DataFrame:
    """One row per underlying per day with loaders.mx.UNDERLYING_COLUMNS: the
    share's bid, ask, midpoint and last, taken in the same pass as its
    options. Volume is empty. The share's quote must be the same on every row
    of a day's capture; if it is not, the file was stitched from two passes."""
    raw = _read(source)
    g = raw.groupby(["date", "underlying"])[["und_bid", "und_ask", "und_last"]]
    if (g.nunique(dropna=False) > 1).any(axis=None):
        raise ValueError("a day's capture holds more than one share quote for a name")
    und = g.first().reset_index().rename(
        columns={"und_bid": "bid", "und_ask": "ask", "und_last": "last"})
    ok = (und["bid"] > 0) & (und["ask"] >= und["bid"])
    und["mid"] = np.where(ok, 0.5 * (und["bid"] + und["ask"]), np.nan)
    und["volume"] = np.nan
    return und[UNDERLYING_COLUMNS].sort_values(["date", "underlying"]).reset_index(drop=True)


def capture_summary(day: date, underlying: str, asked: int, rows: pd.DataFrame,
                    seconds: float, note: str = "") -> dict:
    """One line for the capture log. `rows` is what came back, in
    CAPTURE_COLUMNS. `missed` is what was asked for and returned no row."""
    bid = pd.to_numeric(rows["bid"], errors="coerce")
    ask = pd.to_numeric(rows["ask"], errors="coerce")
    return dict(date=day.isoformat(), underlying=underlying, asked=int(asked),
                returned=int(len(rows)), two_sided=int(((bid > 0) & (ask >= bid)).sum()),
                missed=int(asked - len(rows)), seconds=round(float(seconds), 1), note=note)


def missed_days(log: pd.DataFrame, names: Sequence[str], first: date, last: date,
                venue: str = TSX) -> pd.DataFrame:
    """Trading days from `first` to `last` on which a name has no line in the
    capture log, or a line that returned nothing. One row per (date, name).
    A gap that is not written down gets mistaken for a quiet day."""
    got = set()
    for d, n, returned in zip(log["date"], log["underlying"], log["returned"]):
        if int(returned) > 0:
            got.add((pd.Timestamp(d).date(), str(n)))
    rows = [dict(date=d, underlying=n) for d in trading_days(venue, first, last)
            for n in names if (d, n) not in got]
    return pd.DataFrame(rows, columns=["date", "underlying"])
