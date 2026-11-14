"""
nyvol.py — the vol a New York listing showed, with the currency taken out (C3).

A Canadian company listed in New York has options priced in US dollars. In
US dollars the share moves with the company AND with the Canadian dollar, so
its implied vol carries both. Taking logs of price_USD = price_CAD x
(US dollars per Canadian dollar):

    var(USD) = var(CAD) + var(FX) + 2 rho sd(CAD) sd(FX)

where FX is the value of one Canadian dollar in US dollars and rho the
correlation of the share's Toronto return with it (build sheet, deferred
item 2). Solving for sd(CAD):

    sd(CAD) = -rho sd(FX) + sqrt(var(USD) - var(FX) (1 - rho^2))

The currency's vol and its correlation with the share are not quoted, so
they are MEASURED: realized over the sessions before each day, on the
project's calendar clock (realized.realized_vol). When the square root has
no real answer (the currency explains more variance than the option shows),
no vol is returned and the caller counts it, never guesses one.

The replay uses the CHANGE in this vol from an episode's first close at the
position's own maturity, added to the position's own vol today. It moves the
whole smile in parallel: a skew that steepened in the episode is not
captured, and the report says so.

Nothing here reads a file.
"""

from __future__ import annotations

import math
from datetime import date

import numpy as np
import pandas as pd

from realized import realized_vol

FX_WINDOW = 60          # sessions behind each day's currency vol and correlation


def usdcad_from_gbp(fx: pd.DataFrame) -> pd.Series:
    """Compustat quotes every currency against the pound. Canadian dollars per
    US dollar is the ratio of the two rates, by date."""
    w = fx.pivot_table(index="datadate", columns="tocurd", values="exratd", aggfunc="last")
    s = (w["CAD"] / w["USD"]).dropna()
    s.index = pd.to_datetime(s.index)
    return s.sort_index().rename("usdcad")


def cad_vol(usd_vol: float, fx_vol: float, rho: float) -> float | None:
    """The share's vol in Canadian dollars from its vol in US dollars."""
    vals = (usd_vol, fx_vol, rho)
    if not all(math.isfinite(x) for x in vals) or usd_vol <= 0 or fx_vol < 0 or abs(rho) > 1:
        return None
    inside = usd_vol ** 2 - fx_vol ** 2 * (1.0 - rho ** 2)
    if inside < 0:
        return None
    out = -rho * fx_vol + math.sqrt(inside)
    return out if out > 0 else None


def fx_terms(share_tr: pd.Series, usdcad: pd.Series, day: date,
             window: int = FX_WINDOW) -> tuple[float, float]:
    """(fx vol, correlation) over the `window` sessions up to and including
    `day`: the currency is the value of a Canadian dollar in US dollars, so
    its log return is minus that of USD/CAD."""
    s = np.log1p(share_tr.astype(float))
    f = -np.log(usdcad.astype(float)).diff()
    # the window is the `window` sessions ENDING on the day; a share with no
    # data in them gives nothing, rather than a figure from months before
    f = f[f.index <= pd.Timestamp(day)].tail(window)
    both = pd.concat([s.reindex(f.index), f], axis=1, keys=["s", "f"]).dropna()
    if len(both) < window // 2:
        return float("nan"), float("nan")
    span = (both.index[-1] - both.index[0]).days
    T = (span + span / max(len(both) - 1, 1)) / 365.0     # the window's calendar length
    return realized_vol(both["f"].to_numpy(), T), float(both["s"].corr(both["f"]))


def atm_usd_vol(surface: pd.DataFrame, day: date, days: float) -> float:
    """At-the-money vol on `day` at `days` to expiry: the call and put at
    delta 50 averaged, read along days to expiry between the standard
    maturities (flat beyond the first and last). nan if the day has none."""
    s = surface[surface["date"] == pd.Timestamp(day)]
    if s.empty:
        return float("nan")
    by = s.groupby("days")["impl_volatility"].mean().dropna().sort_index()
    if by.empty:
        return float("nan")
    return float(np.interp(days, by.index.to_numpy(float), by.to_numpy(float)))
