"""
hedge.py — hedge ratios and what is left after the hedge (build sheet C4).

The question a desk asks of a hedge is how much risk it would have removed if
it had actually been put on. That rules out the usual shortcut. A ratio
fitted on the same days it is judged on has already seen those days, so it
always looks better than it could have been. Here the ratio used on a day is
fitted only on the days before it.

Everything works on daily returns. It computes; it does not read files.
"""

from __future__ import annotations

from typing import Mapping

import numpy as np
import pandas as pd


def _ols(y: np.ndarray, X: np.ndarray) -> np.ndarray:
    """Slopes of y on the columns of X, with an intercept that is fitted and
    then thrown away (a hedge has no intercept; leaving it out of the fit
    would push a drift into the slopes)."""
    A = np.column_stack([np.ones(len(y)), X])
    coef, *_ = np.linalg.lstsq(A, y, rcond=None)
    return coef[1:]


def rolling_ratio(y: pd.Series, X: pd.DataFrame, window: int = 250) -> pd.DataFrame:
    """
    For each day, the hedge ratios fitted on the `window` days BEFORE it: the
    amount of each hedge whose returns best offset the position's. Empty
    until a full window exists.

    With one hedge this is the position's beta to it. With two (the index and
    a sector fund) they are fitted together, so the sector ratio is what the
    sector explains beyond the index.

    How well a ratio is known: with the hedge explaining about half the
    variance, a ratio from n days is good to roughly 1/sqrt(n) of itself, so
    about 6% on 250 days.
    """
    d = pd.concat([y.rename("_y"), X], axis=1).dropna()
    yv, Xv = d["_y"].to_numpy(), d[X.columns].to_numpy()
    out = np.full((len(d), X.shape[1]), np.nan)
    for t in range(window, len(d)):
        out[t] = _ols(yv[t - window:t], Xv[t - window:t])       # rows t-window .. t-1
    return pd.DataFrame(out, index=d.index, columns=X.columns)


def full_sample_ratio(y: pd.Series, X: pd.DataFrame) -> dict[str, float]:
    """One ratio per hedge fitted on EVERY day: hindsight. Nobody could have
    traded it. It is shown beside the honest number so the gap is visible."""
    d = pd.concat([y.rename("_y"), X], axis=1).dropna()
    return dict(zip(X.columns, _ols(d["_y"].to_numpy(), d[X.columns].to_numpy())))


def hedge_residual(y: pd.Series, X: pd.DataFrame,
                   ratios: pd.DataFrame | Mapping[str, float]) -> pd.Series:
    """The position's return minus ratio times each hedge's return, day by
    day. `ratios` is rolling_ratio's output or one fixed number per hedge.
    Empty on days with no ratio yet."""
    d = pd.concat([y.rename("_y"), X], axis=1).dropna()
    if isinstance(ratios, pd.DataFrame):
        b = ratios.reindex(d.index)
    else:
        b = pd.DataFrame({c: ratios[c] for c in X.columns}, index=d.index)
    return (d["_y"] - (b[X.columns] * d[X.columns]).sum(axis=1, min_count=X.shape[1])).rename(y.name)


def variance_removed(y: pd.Series, residual: pd.Series) -> float:
    """Share of the position's daily variance the hedge took away, measured
    only on days the hedge was on: 1 - var(residual) / var(position)."""
    r = residual.dropna()
    return float(1.0 - r.var() / y.reindex(r.index).var())


def autocorrelation(x: pd.Series, lag: int = 1) -> float:
    """Correlation of a series with itself `lag` days earlier. Near zero
    means yesterday's residual says nothing about today's, which is what
    noise looks like. Clearly positive or negative means there is a pattern
    left that something could hedge or trade."""
    x = x.dropna()
    return float(x.autocorr(lag))


# --------------------------------------------------------------------------
# is what is left really just noise? (build sheet C4, third question)
# --------------------------------------------------------------------------

LJUNG_BOX_5PCT = {5: 11.07, 10: 18.31, 20: 31.41}     # chi-square 5% points


def ljung_box(x: pd.Series, lags: int = 10) -> tuple[float, bool]:
    """
    (Q, pattern) where Q is the Ljung-Box statistic over the first `lags`
    autocorrelations and `pattern` is True if Q is above its 5% point.

    One autocorrelation near zero says little: a residual can have none at
    one day and plenty at three. Q adds up the squared autocorrelations at
    every lag up to `lags`, scaled so that for pure noise it stays below the
    5% point nineteen times in twenty. `lags` must be 5, 10 or 20.
    """
    if lags not in LJUNG_BOX_5PCT:
        raise ValueError(f"lags must be one of {sorted(LJUNG_BOX_5PCT)}")
    v = x.dropna().to_numpy(dtype=float)
    n = len(v)
    if n <= lags + 1:
        return float("nan"), False
    e = v - v.mean()
    den = float(e @ e)
    q = sum((float(e[k:] @ e[:-k]) / den) ** 2 / (n - k) for k in range(1, lags + 1))
    q *= n * (n + 2.0)
    return float(q), bool(q > LJUNG_BOX_5PCT[lags])


def expected_shortfall(x: pd.Series, level: float = 0.975) -> float:
    """
    The average loss on the worst (1 - level) of days, as a positive number
    (0.03 = a 3% loss). At 97.5% it is the mean of the worst 1 day in 40.

    Variance treats a quiet day and a crash day by the same rule. A hedge can
    remove most of the variance and still leave the worst days nearly as bad,
    because those are the days the hedge ratio is most wrong. This is the
    measure that would show it. NaN with fewer than 40 days.
    """
    v = np.sort(x.dropna().to_numpy(dtype=float))
    k = int(np.floor(len(v) * (1.0 - level)))
    if len(v) < 40 or k < 1:
        return float("nan")
    return float(-v[:k].mean())


def variance_removed_error(y: pd.Series, residual: pd.Series, block: int = 20,
                           reps: int = 300, seed: int = 0) -> float:
    """
    Standard error of variance_removed, by resampling the days in BLOCKS.

    The share of variance removed is itself an estimate. Resampling single
    days would treat each as independent and understate the error, because
    volatile days come in runs. So whole blocks of `block` consecutive days
    are drawn with replacement, the statistic is recomputed `reps` times, and
    the scatter of those is the error. `seed` fixes the draw so the number
    reproduces.
    """
    r = residual.dropna()
    yy = y.reindex(r.index).to_numpy(dtype=float)
    rr = r.to_numpy(dtype=float)
    n = len(rr)
    if n < 2 * block:
        return float("nan")
    rng = np.random.default_rng(seed)
    starts = rng.integers(0, n - block + 1, size=(reps, int(np.ceil(n / block))))
    idx = (starts[:, :, None] + np.arange(block)[None, None, :]).reshape(reps, -1)[:, :n]
    return float(np.std(1.0 - rr[idx].var(axis=1, ddof=1) / yy[idx].var(axis=1, ddof=1), ddof=1))


def by_regime(y: pd.Series, residual: pd.Series, market: pd.Series, window: int = 20,
              top: float = 0.2) -> dict[str, float]:
    """
    Variance removed on calm days and on stressed days, and the count of each.

    A day is "stressed" if the market's realised volatility over the `window`
    days BEFORE it is in the top `top` share of the sample (the cut-off is
    taken over the whole sample, which is hindsight; it labels days, it is
    not a trading rule). A hedge that works on average and fails in stress
    is the one that matters to know about.
    """
    r = residual.dropna()
    vol = market.rolling(window).std().shift(1).reindex(r.index)
    ok = vol.notna()
    cut = vol[ok].quantile(1.0 - top)
    out = {}
    for label, mask in (("calm", ok & (vol <= cut)), ("stressed", ok & (vol > cut))):
        rr, yy = r[mask], y.reindex(r.index)[mask]
        out[label] = float(1.0 - rr.var() / yy.var()) if len(rr) > 20 else float("nan")
        out[f"{label} days"] = int(mask.sum())
    return out


def compounded(x: pd.Series) -> float:
    """Total return over a run of daily returns: (1 + r1)(1 + r2)... - 1.
    NaN if any day is missing, so a gap is never read as a flat day. Adding
    daily returns instead overstates a long fall (it can pass -100%)."""
    if len(x) == 0 or x.isna().any():
        return float("nan")
    return float(np.prod(1.0 + x.to_numpy(dtype=float)) - 1.0)
