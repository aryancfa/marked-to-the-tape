"""
optionm.py — reads OptionMetrics (IvyDB US) files downloaded from WRDS, for the
engine check (build sheet B8).

What this is for
----------------
OptionMetrics computes its own implied vol for every listed US option, with
its own American-option model. The seven Canadian names also list in New
York, so their options are in that file. Pricing those same quotes with this
project's engine, from OptionMetrics' own share price, rates and dividends,
and comparing the two vols is a check against a second model that owes
nothing to this code or to the Montreal Exchange file.

What it is not
--------------
* Not Montreal Exchange data. It is a different exchange, in US dollars.
* Not on the same dates: this WRDS subscription ends 29 August 2025 and the
  Montreal files start 8 May 2026.

Layout, as found in the files (scripts/wrds_catalogue.py):
  opprcd  one row per option per day. strike_price is the strike times 1000.
          best_bid / best_offer are the closing quote. impl_volatility is
          theirs, empty where they could not compute one. There is no
          settlement price.
  secprd  the share's daily close.
  zerocd  zero-coupon rates by days, continuously compounded, in percent.
  distrd  distributions. distr_type 1 is a regular cash dividend. The amount
          is in the currency named on the row, which for these names is
          usually Canadian dollars.

The data is licensed and stays under data/raw/wrds/. Tests use made-up files.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Mapping

import numpy as np
import pandas as pd

from rates import Curve

TRADED, QUOTED, NO_PRICE = "traded", "quoted", "no_price"


def load_optionm_options(path: str | Path, underlying: str) -> pd.DataFrame:
    """
    Options in the shape surface.invert_chain takes, so the same code inverts
    them. `underlying` is the project's name for the listing, e.g. "RY:US".

    mark is the quote midpoint where the quote is two-sided and empty
    otherwise: there is no settlement to fall back on. om_iv is OptionMetrics'
    own implied vol as a decimal.
    """
    raw = pd.read_csv(path, parse_dates=["date", "exdate"], low_memory=False)
    bid, ask = raw["best_bid"].astype(float), raw["best_offer"].astype(float)
    two_sided = (bid > 0) & (ask > bid)
    volume = raw["volume"].fillna(0).astype(int)
    out = pd.DataFrame({
        "date": raw["date"], "underlying": underlying, "expiry": raw["exdate"],
        "strike": raw["strike_price"].astype(float) / 1000.0,
        "call_put": raw["cp_flag"].astype(str),
        "bid": bid, "ask": ask, "settle": np.nan, "volume": volume,
        "open_interest": raw["open_interest"].fillna(0).astype(int),
        "traded": volume > 0, "two_sided": two_sided})
    bad = sorted(set(out["call_put"]) - {"C", "P"})
    if bad:
        raise ValueError(f"unknown cp_flag values {bad}")
    out["tier"] = np.where(~two_sided, NO_PRICE, np.where(out["traded"], TRADED, QUOTED))
    out["mark"] = np.where(two_sided, 0.5 * (bid + ask), np.nan)
    out["mark_source"] = np.where(two_sided, "mid", "none")
    out["om_iv"] = pd.to_numeric(raw["impl_volatility"], errors="coerce").where(two_sided)
    return out.sort_values(["date", "expiry", "strike", "call_put"]).reset_index(drop=True)


def load_optionm_prices(path: str | Path) -> dict[date, float]:
    """The share's close by date."""
    raw = pd.read_csv(path, parse_dates=["date"])
    return {d.date(): float(c) for d, c in zip(raw["date"], raw["close"])}


def load_optionm_curve(path: str | Path, day: date) -> Curve:
    """OptionMetrics' zero curve for `day`, as the Curve the engine takes.
    Raises if the file has no curve for that day: a rate is not carried."""
    raw = pd.read_csv(path, parse_dates=["date"])
    d = raw[raw["date"] == pd.Timestamp(day)].sort_values("days")
    if d.empty:
        raise ValueError(f"no zero curve for {day}")
    pts = tuple((float(n) / 365.0, float(r) / 100.0) for n, r in zip(d["days"], d["rate"]))
    return Curve(day, pts, (("zerocd", day),), source="OptionMetrics zero curve")


def load_optionm_dividends(path: str | Path,
                           cad_per_usd: Mapping[date, float] | None,
                           since: date | None = None) -> list[tuple[date, float]]:
    """
    Regular cash dividends as (ex-date, cash in US dollars), in date order.

    The New York options are in US dollars and these companies declare in
    Canadian dollars, so a Canadian-dollar amount is divided by the exchange
    rate (Canadian dollars per US dollar) on its ex-date. A missing rate
    raises. `since` drops ex-dates before that day, so old dividends that no
    option in use depends on need no rate. Pass None to take every amount at face value: that is wrong, and
    exists only to test whether OptionMetrics itself converts.
    """
    raw = pd.read_csv(path, parse_dates=["ex_date"])
    raw = raw[(raw["distr_type"] == 1) & (raw["cancel_flag"] == 0)].sort_values("ex_date")
    if since is not None:
        raw = raw[raw["ex_date"] >= pd.Timestamp(since)]
    out = []
    for d, amount, cur in zip(raw["ex_date"], raw["amount"], raw["currency"]):
        d = d.date()
        amount = float(amount)
        if cad_per_usd is not None and cur == "CAD":
            if d not in cad_per_usd:
                raise ValueError(f"no exchange rate for the ex-date {d}")
            amount /= cad_per_usd[d]
        elif cad_per_usd is not None and cur != "USD":
            raise ValueError(f"dividend on {d} is in {cur}; only CAD and USD are handled")
        out.append((d, amount))
    return out
