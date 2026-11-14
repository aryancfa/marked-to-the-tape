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

The replay uses the CHANGE in this vol from an episode's first close, at the
position's own maturity and at its own strike's place on the smile (the
25-delta put, the money and the 25-delta call, interpolated in delta), added
to the position's own vol today. So a skew that steepened in the episode
moves a low strike more than the money. Beyond the 25-delta points the smile
is held flat, so a deep strike moves as the 25-delta one did.

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


SKEW_POINTS = ((-25, "P", 0.75), (50, None, 0.50), (25, "C", 0.25))
# (OptionMetrics delta, side, call-equivalent delta). The 25-delta put is a LOW
# strike, which a call there would see as 75 delta; the 25-delta call a HIGH
# one; delta 50 on either side is the money.


def strike_call_delta(spot: float, strike: float, T: float, vol: float) -> float:
    """Where a strike sits on the smile, as the delta a call there would
    have: N(d1) with rates and dividends left out, which places a strike
    well enough to read a smile quoted at 25 and 50 delta. Recomputed on
    each replay day from the moved share price, so a fixed strike slides
    along the smile as the share moves (sticky strike), as a held option
    does."""
    if not (spot > 0 and strike > 0 and T > 0 and vol > 0):
        return float("nan")
    d1 = (math.log(spot / strike) + 0.5 * vol * vol * T) / (vol * math.sqrt(T))
    return 0.5 * (1.0 + math.erf(d1 / math.sqrt(2.0)))


def smile_usd_vol(surface: pd.DataFrame, day: date, days: float, cdelta: float) -> float:
    """The New York vol on `day` at `days` to expiry and at the strike whose
    call delta is `cdelta`: read at the 25-delta put (call delta 0.75), the
    money (0.5) and the 25-delta call (0.25), each along days to expiry, then
    linearly in delta between them and flat beyond. This is what lets the
    replay move a low strike's vol by what low strikes did, not by what the
    money did. nan if the day lacks any of the three."""
    s = surface[surface["date"] == pd.Timestamp(day)]
    if s.empty or "delta" not in s:
        return float("nan")
    xs, ys = [], []
    for d, side, cd in SKEW_POINTS:
        pick = s[s["delta"].abs() == 50] if side is None else s[s["delta"] == d]
        by = pick.groupby("days")["impl_volatility"].mean().dropna().sort_index()
        if by.empty:
            return float("nan")
        xs.append(cd)
        ys.append(float(np.interp(days, by.index.to_numpy(float), by.to_numpy(float))))
    order = np.argsort(xs)
    return float(np.interp(cdelta, np.array(xs)[order], np.array(ys)[order]))


def atm_usd_vol(surface: pd.DataFrame, day: date, days: float) -> float:
    """At-the-money vol on `day` at `days` to expiry: the call and put at
    delta 50 averaged, read along days to expiry between the standard
    maturities (flat beyond the first and last). nan if the day has none."""
    s = surface[surface["date"] == pd.Timestamp(day)]
    if "delta" in s:
        s = s[s["delta"].abs() == 50]
    if s.empty:
        return float("nan")
    by = s.groupby("days")["impl_volatility"].mean().dropna().sort_index()
    if by.empty:
        return float("nan")
    return float(np.interp(days, by.index.to_numpy(float), by.to_numpy(float)))
