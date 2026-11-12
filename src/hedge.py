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
