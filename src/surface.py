"""
surface.py — turn one day's option table into implied vols (build sheet B3-B5).

Takes rows from loaders.mx, prices from voleng, dates from tradingcal. It
computes; it does not read files or draw charts.

Two different error bars, and they answer different questions
-------------------------------------------------------------
1. The QUOTE bar: half the bid-ask spread, in vol points. It says how far
   the vol could move before someone could trade against it. On RY it is
   about 1.3 vol points per option.
2. The SCATTER: how much the measured number actually varies, across strikes
   on one day or across days. On RY the call-put gap scatters by 0.2 to 0.5
   vol points across strikes, several times tighter than the quote bar,
   because dealers quote wide around a fair value they largely agree on.

The quote bar answers "can I trade on this?". The scatter answers "is the
model's input wrong?". Whether a mean differs from zero is judged against
its standard error (scatter / sqrt(count)), never against the quote bar.

A row that cannot be inverted keeps an empty vol and a reason. Nothing is
ever filled in.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Callable, Mapping, Sequence

import numpy as np
import pandas as pd

from tradingcal import is_monthly_expiry, year_fraction
from voleng import (Reject, crr_american, implied_spot_american,
                    implied_vol_american, screen_quote)

EXPIRED = "expired_or_expiring_today"
NO_MARK = "no_price_to_invert"

DatedDividend = tuple[date, float]      # (ex-dividend date, cash per share)

# The share price drops at the OPEN of the ex-date. Measured in whole days from
# a close, that is one day before the ex-date's own close.
EX_SHIFT_DAYS = 1.0


def dividend_times(valuation: date, divs: Sequence[DatedDividend],
                   ex_shift_days: float = EX_SHIFT_DAYS) -> list[tuple[float, float]]:
    """
    Dated dividends -> (years from valuation, cash). The engine keeps only
    those with 0 < t <= T, so passing the whole schedule is safe.

    The share price opens lower on the ex-date. Counting whole days from the
    valuation close would put the drop at the CLOSE of the ex-date, one
    trading session late. So a dividend still to come is placed
    `ex_shift_days` earlier (1.0 = the previous day's close), but never
    closer than a quarter of a day to the valuation close, so it still
    counts. For an expiry a few days after the ex-date that session is a
    large share of the time left, and it changes whether a call is worth
    exercising early. `ex_shift_days=0` restores the close of the ex-date.

    This is a calendar-day stand-in for the open. It removed about half of
    the gap seen on RY weeklies just after an ex-date; a day and a half
    removed about two thirds, which suggests time should really be counted
    in trading sessions. That larger change has not been made.
    """
    out = []
    for d, cash in divs:
        days = (d - valuation).days
        if days > 0 and ex_shift_days:
            days = max(days - ex_shift_days, 0.25)
        out.append((days / 365.0, cash))
    return out


def _as_date(x) -> date:
    return x.date() if hasattr(x, "date") else x


def invert_chain(options: pd.DataFrame, spot: float,
                 r: float | Callable[[float], float],
                 divs: Sequence[DatedDividend], b: float = 0.0,
                 n: int = 400, exact_bars: bool = False,
                 ex_shift_days: float = EX_SHIFT_DAYS) -> pd.DataFrame:
    """
    Invert every row of ONE underlying on ONE date.

    `options` is a slice of loaders.mx.load_mx_options. `r` is either one
    rate for every row or a function of time to expiry (a rates.Curve), in
    which case each row is priced at the rate for its own expiry.

    Returns the rows with:
      T               years to expiry
      r               the rate used for this row
      iv              vol implied by the mark (empty if rejected)
      reason          Reject code, or "ok"
      half_spread_vol the quote bar: half the bid-ask spread, in vol units
      iv_bid, iv_ask  vols at the bid and the ask (two-sided rows)

    The quote bar is the half-spread in dollars divided by vega, the dollars
    one unit of vol is worth. The solver has already measured that vega, so
    the bar costs nothing extra, and iv_bid / iv_ask are iv minus and plus
    the bar. `exact_bars=True` inverts the bid and the ask separately
    instead: three times the work, and the bar goes empty where the bid sits
    below exercise value.

    Screening: a two-sided quote is evidence on its own, so quoted rows are
    not thrown out for having no volume or open interest. Rows marked from a
    settlement keep the activity screen, since an untraded settlement with no
    open interest is the exchange's model and nothing else.
    """
    if options["date"].nunique() > 1 or options["underlying"].nunique() > 1:
        raise ValueError("invert_chain takes one underlying on one date")
    out = options.copy()
    if out.empty:
        return out.assign(T=[], r=[], iv=[], reason=[], iv_bid=[], iv_ask=[],
                          half_spread_vol=[])
    rate = r if callable(r) else (lambda T, _r=r: _r)
    valuation = _as_date(out["date"].iloc[0])
    dts = dividend_times(valuation, divs, ex_shift_days)

    rows = []
    for o in out.itertuples(index=False):
        T = year_fraction(valuation, _as_date(o.expiry))
        is_call = o.call_put == "C"
        rT = rate(max(T, 0.0))
        rec = dict(T=T, r=rT, iv=np.nan, reason=Reject.OK, iv_bid=np.nan,
                   iv_ask=np.nan, half_spread_vol=np.nan)
        rows.append(rec)
        if T <= 0.0:
            rec["reason"] = EXPIRED
            continue
        if not np.isfinite(o.mark):
            rec["reason"] = NO_MARK
            continue
        if o.two_sided:
            why = screen_quote(o.mark, spot, o.strike, T, rT, is_call,
                               bid=o.bid, ask=o.ask)
        else:
            why = screen_quote(o.mark, spot, o.strike, T, rT, is_call,
                               volume=int(o.volume),
                               open_interest=int(o.open_interest))
        if why != Reject.OK:
            rec["reason"] = why
            continue
        res = implied_vol_american(o.mark, spot, o.strike, T, rT, b, dts, is_call, n)
        rec["reason"] = res.reason
        if res.iv is None:
            continue
        rec["iv"] = res.iv
        if not o.two_sided:
            continue
        if exact_bars:
            lo = implied_vol_american(o.bid, spot, o.strike, T, rT, b, dts, is_call, n)
            hi = implied_vol_american(o.ask, spot, o.strike, T, rT, b, dts, is_call, n)
            if lo.iv is not None:
                rec["iv_bid"] = lo.iv
            if hi.iv is not None:
                rec["iv_ask"] = hi.iv
            if lo.iv is not None and hi.iv is not None:
                rec["half_spread_vol"] = 0.5 * (hi.iv - lo.iv)
        else:
            hs = 0.5 * (o.ask - o.bid) / (res.vega * 100.0)
            rec["half_spread_vol"] = hs
            rec["iv_bid"], rec["iv_ask"] = res.iv - hs, res.iv + hs

    extra = pd.DataFrame(rows, index=out.index)
    return pd.concat([out, extra], axis=1)


def rejection_tally(inverted: pd.DataFrame) -> pd.Series:
    """Rows per reason, "ok" included. Shown beside every chart (B4)."""
    return inverted["reason"].value_counts()


def call_put_gap(inverted: pd.DataFrame) -> pd.DataFrame:
    """
    Call vol minus put vol at each strike and expiry where both inverted (B5).

    Columns: expiry, strike, T, iv_call, iv_put, gap (in VOL POINTS),
    error_bar (the two half-spreads added, in vol points; empty unless both
    sides are two-sided), gap_in_error_bars, tier_call, tier_put.

    Reading it: with the right share price, rate and dividends the gap is zero
    at every strike. A gap that is the same at every strike of an expiry
    means a carry input is wrong for that expiry, because real skew bends
    with strike and this does not. |gap_in_error_bars| below 1 means the
    quotes cannot tell the two vols apart.
    """
    ok = inverted[inverted["iv"].notna()]
    keep = ["expiry", "strike", "T", "iv", "half_spread_vol", "tier"]
    c = ok[ok["call_put"] == "C"][keep]
    p = ok[ok["call_put"] == "P"][keep]
    m = c.merge(p, on=["expiry", "strike", "T"], suffixes=("_call", "_put"))
    m["gap"] = (m["iv_call"] - m["iv_put"]) * 100.0
    m["error_bar"] = (m["half_spread_vol_call"] + m["half_spread_vol_put"]) * 100.0
    m["gap_in_error_bars"] = m["gap"] / m["error_bar"]
    cols = ["expiry", "strike", "T", "iv_call", "iv_put", "gap", "error_bar",
            "gap_in_error_bars", "tier_call", "tier_put"]
    return m[cols].sort_values(["expiry", "strike"]).reset_index(drop=True)


def front_pairs(options: pd.DataFrame, approx_spot: float, min_days: int = 7,
                k: int = 5, monthly_only: bool = True,
                ) -> tuple[date | None, list[tuple[float, float, float]]]:
    """
    Pick the strikes used to imply the share price: the nearest MONTHLY expiry
    at least `min_days` away, and the `k` strikes closest to `approx_spot`
    where BOTH the call and the put carry a two-sided quote.

    Monthly, not weekly: on the first RY day the nearest weekly had the widest
    quotes on the chart (a 4.4 vol point error bar against 3.4 on the monthly
    a week later), and a share price implied from wide quotes is itself
    uncertain. Five strikes, not three, so one bad quote moves the answer
    less. `monthly_only=False` restores the nearest expiry of any kind.

    Returns (expiry, [(strike, call_mid, put_mid), ...]); (None, []) if no
    expiry qualifies. `approx_spot` only chooses which strikes are near the
    money; its exact value does not enter the result.
    """
    valuation = options["date"].iloc[0]
    q = options[options["two_sided"]]
    days = (q["expiry"] - valuation).dt.days
    q = q[days >= min_days]
    for expiry in sorted(q["expiry"].unique()):
        if monthly_only and not is_monthly_expiry(_as_date(pd.Timestamp(expiry))):
            continue
        e = q[q["expiry"] == expiry]
        wide = e.pivot_table(index="strike", columns="call_put", values="mark")
        if not {"C", "P"} <= set(wide.columns):
            continue
        wide = wide.dropna(subset=["C", "P"])
        if len(wide) < k:
            continue
        near = wide.loc[(wide.index.to_series() - approx_spot).abs()
                        .sort_values().index[:k]].sort_index()
        return _as_date(pd.Timestamp(expiry)), [
            (float(K), float(row["C"]), float(row["P"]))
            for K, row in near.iterrows()]
    return None, []


# --------------------------------------------------------------------------
# one day, start to finish
# --------------------------------------------------------------------------

def near_the_money(options: pd.DataFrame, spot: float, moneyness: float = 0.12,
                   min_days: int = 7, max_days: int = 400) -> pd.DataFrame:
    """Rows with strike within `moneyness` of the share price and between
    `min_days` and `max_days` calendar days to expiry, both ends included.
    Further out, vega is small and the quotes say little about vol."""
    days = (options["expiry"] - options["date"]).dt.days
    return options[((options["strike"] / spot - 1.0).abs() <= moneyness)
                   & days.between(min_days, max_days)]


def carry_label(expiry: date, front: date | None,
                first_projected: date | None) -> str:
    """
    How far the carry inputs behind an expiry can be trusted:
      calibrated          the share price was implied from this expiry, so
                          its call-put gap is zero by construction
      declared only       every dividend before it has been declared
      projected dividend  at least one dividend before it is our projection
    """
    if front is not None and expiry == front:
        return "calibrated"
    if first_projected is not None and expiry >= first_projected:
        return "projected dividend"
    return "declared only"


def gap_summary(gap: pd.DataFrame, valuation: date, front: date | None = None,
                first_projected: date | None = None) -> pd.DataFrame:
    """
    One row per expiry from call_put_gap output, indexed by expiry date.

      days              calendar days to expiry
      pairs             strikes with both a call vol and a put vol
      mean_gap          mean call-minus-put vol, in vol points
      weighted_gap      the same mean with each strike weighted by
                        1 / (its quote bar squared); the plain mean where no
                        strike has a quote bar
      scatter           standard deviation of the gap across strikes
      std_error         scatter / sqrt(pairs): how well the MEAN is known
      t_stat            mean_gap / std_error
      median_quote_bar  median of the two quotes' half-spreads added
      carry             see carry_label

    A carry error (rate, dividend, share price) moves every strike of an
    expiry the same way, so it shows in mean_gap and not in scatter. |t|
    above about 2 says the mean is not quote noise. Fewer than three strikes
    give no scatter worth the name, so std_error and t_stat stay empty.

    Why weight: a strike quoted five vol points wide tells you far less
    than one quoted one point wide, and its midpoint is often lopsided. A
    plain mean lets a few such strikes drag the expiry. Weighting by one over
    the bar squared is how measurements with known error bars are averaged.
    forward_errors uses the weighted mean.

    The standard error is a LOWER bound. It treats strikes as independent,
    and one dealer's quotes across neighbouring strikes are not. The scatter
    across days (pool_across_days) is the sturdier measure.
    """
    g = gap.assign(expiry=[_as_date(e) for e in gap["expiry"]])
    out = g.groupby("expiry").agg(
        pairs=("gap", "size"), mean_gap=("gap", "mean"), scatter=("gap", "std"),
        median_quote_bar=("error_bar", "median"))
    w = 1.0 / g["error_bar"].where(g["error_bar"] > 0) ** 2
    weighted = (g["gap"] * w).groupby(g["expiry"]).sum(min_count=1) / \
        w.groupby(g["expiry"]).sum(min_count=1)
    out["weighted_gap"] = weighted.fillna(out["mean_gap"])
    out["scatter"] = out["scatter"].where(out["pairs"] >= 3)
    out["std_error"] = out["scatter"] / np.sqrt(out["pairs"])
    out["t_stat"] = out["mean_gap"] / out["std_error"].where(out["std_error"] > 0)
    out.insert(0, "days", [(e - valuation).days for e in out.index])
    out["carry"] = [carry_label(e, front, first_projected) for e in out.index]
    return out[["days", "pairs", "mean_gap", "weighted_gap", "scatter",
                "std_error", "t_stat", "median_quote_bar", "carry"]]


def pool_across_days(daily: pd.DataFrame) -> pd.DataFrame:
    """
    Pool one number per (date, expiry) across days. `daily` needs columns
    date, expiry, mean_gap. Indexed by expiry:

      days_observed, mean_gap, scatter_across_days, std_error, t_stat

    Quote noise changes from day to day and averages out. A wrong dividend
    or rate does not: it pushes the same expiry the same way every day. So a
    mean that stays away from zero across days, measured against its
    day-to-day scatter, is the evidence of a wrong input. One day gives no
    scatter, so its std_error stays empty.

    The standard error assumes days are independent. Neighbouring days share
    slow-moving quotes, so read |t| near 2 with caution.
    """
    d = daily.assign(expiry=[_as_date(e) for e in daily["expiry"]])
    out = d.groupby("expiry").agg(
        days_observed=("mean_gap", "count"), mean_gap=("mean_gap", "mean"),
        scatter_across_days=("mean_gap", "std"))
    out["std_error"] = out["scatter_across_days"] / np.sqrt(out["days_observed"])
    out["t_stat"] = out["mean_gap"] / out["std_error"].where(out["std_error"] > 0)
    return out


def vs_exchange(inverted: pd.DataFrame) -> dict:
    """Our vol minus the exchange's published vol, in vol points, for calls
    and for puts: mean and count of rows where both exist."""
    both = inverted[inverted["iv"].notna() & inverted["mx_iv"].notna()]
    d = (both["iv"] - both["mx_iv"]) * 100.0
    out = {}
    for name, cp in (("calls", "C"), ("puts", "P")):
        x = d[both["call_put"] == cp]
        out[f"{name}_mean"] = float(x.mean()) if len(x) else float("nan")
        out[f"{name}_n"] = int(len(x))
    return out


# --------------------------------------------------------------------------
# from a vol gap to a forward error to a funding spread
# --------------------------------------------------------------------------

def forward_sensitivity(S: float, K: float, T: float, r: float, sigma: float,
                        divs: Sequence[tuple[float, float]], n: int = 400,
                        bump: float = 1e-3) -> float:
    """
    Vol points of call-minus-put gap produced when the market's forward price
    sits ONE BASIS POINT above the model's. NaN if it cannot be measured.

    Measured, not approximated: price the call and the put with the share
    price raised by `bump` (which raises the forward by the same proportion),
    invert both at the unbumped price, and see what gap appears.
    """
    up = S * (1.0 + bump)
    c = crr_american(up, K, T, r, 0.0, sigma, divs, True, n)
    p = crr_american(up, K, T, r, 0.0, sigma, divs, False, n)
    ic = implied_vol_american(c, S, K, T, r, 0.0, divs, True, n).iv
    ip = implied_vol_american(p, S, K, T, r, 0.0, divs, False, n).iv
    if ic is None or ip is None:
        return float("nan")
    return (ic - ip) * 100.0 / (bump * 1e4)


def forward_errors(gap: pd.DataFrame, summary: pd.DataFrame, spot: float,
                   rate: Callable[[float], float],
                   divs: Sequence[DatedDividend], valuation: date,
                   n: int = 400, ex_shift_days: float = EX_SHIFT_DAYS) -> list[float]:
    """
    For each expiry in `summary`: how far the market's forward sits above the
    model's, in basis points of the share price.

    A vol gap of 0.25 points means something different at one month than at
    ten. A forward error is the same thing at every maturity, so it can be
    compared across expiries: one that grows in step with time points to the
    rate, and one that jumps at an ex-dividend date points to that dividend.
    It is the expiry's weighted mean gap divided by forward_sensitivity at
    the strike nearest the share price.
    """
    dts = dividend_times(valuation, divs, ex_shift_days)
    expiry = [_as_date(e) for e in gap["expiry"]]
    out = []
    for e, row in summary.iterrows():
        g = gap[[x == e for x in expiry]]
        atm = g.iloc[(g["strike"] - spot).abs().to_numpy().argmin()]
        T = year_fraction(valuation, e)
        sens = forward_sensitivity(spot, float(atm["strike"]), T, rate(T),
                                   0.5 * (atm["iv_call"] + atm["iv_put"]), dts, n)
        out.append(row["weighted_gap"] / sens if sens and np.isfinite(sens) else np.nan)
    return out


def fit_funding_spread(summary: pd.DataFrame, front: date,
                       min_expiries: int = 2) -> float | None:
    """
    One day's estimate of how far the market's funding rate sits above the
    model's, per year (0.002 = 20 basis points). None if it cannot be fitted.

    A funding rate that is too low by s makes the forward too low by s times
    the time beyond the calibrated expiry (where the error is zero because
    the share price was implied there). So fit a straight line through zero
    to forward error against that time; its slope is s.

    Only monthly expiries that rest on declared dividends are used. A
    projected dividend that is too small looks exactly like a rate that is
    too high, and the two could not be told apart.
    """
    xs, ys = [], []
    for e, row in summary.iterrows():
        if (row["carry"] == "declared only" and e > front and is_monthly_expiry(e)
                and np.isfinite(row["fwd_error_bp"])):
            xs.append(year_fraction(front, e))
            ys.append(row["fwd_error_bp"])
    if len(xs) < min_expiries:
        return None
    x, y = np.array(xs), np.array(ys)
    return float((x * y).sum() / (x * x).sum()) / 1e4


def trailing_spread(estimates: Mapping[date, float | None], day: date,
                    min_days: int = 20) -> float | None:
    """
    The funding spread to USE on `day`: the mean of the daily estimates from
    days strictly before it. None until `min_days` of them exist, and then no
    spread is applied.

    Earlier days only, the same rule as the hedge ratios. A spread fitted on
    a day and applied to that same day would close the call-put gap by
    construction, and the chart would then be the model checking itself.
    """
    past = [s for d, s in estimates.items() if d < day and s is not None]
    return float(np.mean(past)) if len(past) >= min_days else None


@dataclass
class DayResult:
    valuation: date
    front: date | None              # expiry the share price was implied from
    spot: float | None
    spot_reason: str
    pairs_used: int
    inverted: pd.DataFrame | None   # None when no share price was implied
    gap: pd.DataFrame | None
    summary: pd.DataFrame | None
    funding_spread: float | None = None     # this day's own estimate, per year


def analyse_day(options: pd.DataFrame, approx_spot: float,
                rate: float | Callable[[float], float],
                divs: Sequence[DatedDividend],
                first_projected: date | None = None, moneyness: float = 0.12,
                min_days: int = 7, max_days: int = 400, bracket: float = 0.015,
                n: int = 400, exact_bars: bool = False,
                ex_shift_days: float = EX_SHIFT_DAYS) -> DayResult:
    """
    One underlying on one date, start to finish:

      1. imply the share price from the nearest monthly expiry's five
         near-the-money call-put pairs, searching within `bracket` of
         `approx_spot` (the file's price, used only as a starting range)
      2. invert the near-the-money rows at that share price
      3. take call minus put vol by strike and summarise it by expiry

    If no share price can be implied the result says why and carries no
    vols. Nothing downstream runs on a guessed share price.
    """
    valuation = _as_date(options["date"].iloc[0])
    r = rate if callable(rate) else (lambda T, _r=rate: _r)
    front, pairs = front_pairs(options, approx_spot, min_days=min_days)
    if front is None:
        return DayResult(valuation, None, None, Reject.TOO_FEW_PAIRS, 0,
                         None, None, None)
    T_front = year_fraction(valuation, front)
    res = implied_spot_american(pairs, T_front, r(T_front), 0.0,
                                dividend_times(valuation, divs, ex_shift_days),
                                approx_spot * (1.0 - bracket),
                                approx_spot * (1.0 + bracket), n=n)
    if res.spot is None:
        return DayResult(valuation, front, None, res.reason, res.pairs_used,
                         None, None, None)
    near = near_the_money(options, res.spot, moneyness, min_days, max_days)
    inverted = invert_chain(near, res.spot, r, divs, n=n, exact_bars=exact_bars,
                            ex_shift_days=ex_shift_days)
    gap = call_put_gap(inverted)
    summary = gap_summary(gap, valuation, front, first_projected)
    summary["fwd_error_bp"] = forward_errors(gap, summary, res.spot, r, divs,
                                             valuation, n, ex_shift_days)
    return DayResult(valuation, front, res.spot, Reject.OK, res.pairs_used,
                     inverted, gap, summary,
                     fit_funding_spread(summary, front))
