"""
oilfactor.py — the WCS discount as a factor of its own (C3).

Western Canadian Select is the heavy crude most Canadian oil sands output is
sold as. It trades at a discount to WTI that has swung from a few dollars to
over $40 a barrel, mostly for reasons of its own (pipeline space, refinery
outages), not because oil in general moved. A producer selling heavy crude
loses revenue when it widens, but one that upgrades or refines heavy crude
gains on cheaper feedstock, so the net effect on a share is an empirical
question, and one an index or a WTI move cannot answer.

THE FACTOR is the change in log(WCS / WTI): the share of a barrel's value the
discount takes, month on month. Measured in dollars a barrel instead, it
showed nothing; as a share it is the economically natural measure (revenue
moves with WCS) but it was chosen after both were tried, which the report
says.

THE FIT: each name's monthly return regressed on the index's, WTI's (log
change) and the discount's, together, so the discount's coefficient is what
it adds once the market and oil itself are allowed for. Alberta publishes
MONTHLY AVERAGE prices, so the share returns are monthly averages too
(average to average); a month-end return against an average price would
blur the timing. Ordinary least squares, plain standard errors: monthly
returns show little dependence from one month to the next.

Nothing here reads a file except load_alberta_oil.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

FACTORS = ("index", "wti", "discount")


def load_alberta_oil(path: str | Path) -> pd.DataFrame:
    """Alberta's economic data service file (table OilPrices, types WCS and
    WTI) -> monthly WTI and WCS in US dollars a barrel, by month. Months
    without both are left out, never filled."""
    rows = json.loads(Path(path).read_text(encoding="utf-8"))
    d = pd.DataFrame(rows)
    d.columns = [c.strip() for c in d.columns]
    w = d.pivot_table(index="Date", columns="Type", values="Value", aggfunc="last")
    w.index = pd.to_datetime(w.index).to_period("M")
    missing = {"WTI", "WCS"} - set(w.columns)
    if missing:
        raise ValueError(f"no {', '.join(sorted(missing))} in {Path(path).name}")
    return w[["WTI", "WCS"]].dropna().sort_index()


def monthly_average_returns(daily: pd.DataFrame) -> pd.DataFrame:
    """Daily total returns -> month-to-month log change of each column's
    monthly AVERAGE level. A day with no return adds no move (the loaders
    keep one-session returns only, so such a day is rare and is a gap)."""
    level = (1.0 + daily.fillna(0.0)).cumprod()
    first = daily.notna().cummax()                # before a column starts, no level
    level = level.where(first)
    avg = level.groupby(level.index.to_period("M")).mean()
    return np.log(avg).diff()


def oil_factors(oil: pd.DataFrame, index_monthly: pd.Series) -> pd.DataFrame:
    """The three factors by month: index (average-to-average log change),
    WTI (log change) and the discount (change in log(WCS / WTI))."""
    return pd.DataFrame({"index": index_monthly,
                         "wti": np.log(oil["WTI"]).diff(),
                         "discount": np.log(oil["WCS"] / oil["WTI"]).diff()}).dropna()


@dataclass
class Fit:
    beta: dict
    se: dict
    months: int

    def t(self, factor: str) -> float:
        return self.beta[factor] / self.se[factor] if self.se[factor] > 0 else float("nan")


def fit(y: pd.Series, X: pd.DataFrame, min_months: int = 36) -> Fit:
    """Ordinary least squares of y on the factors with a constant."""
    y = y.reindex(X.index).dropna()
    if len(y) < min_months:
        raise ValueError(f"{y.name}: {len(y)} months, too few to fit (need {min_months})")
    A = np.column_stack([np.ones(len(y)), X.loc[y.index, list(FACTORS)].to_numpy(float)])
    b, *_ = np.linalg.lstsq(A, y.to_numpy(float), rcond=None)
    e = y.to_numpy(float) - A @ b
    cov = np.linalg.inv(A.T @ A) * (e @ e) / (len(y) - A.shape[1])
    se = np.sqrt(np.diag(cov))
    return Fit(beta=dict(zip(FACTORS, b[1:])), se=dict(zip(FACTORS, se[1:])), months=len(y))


def widening_cases(factors: pd.DataFrame, flat: float = 0.03, share: float = 0.05):
    """Two scenarios with WTI flat and the discount widening:
      the month since the factors start with |WTI change| under `flat` in
      which the discount widened most, and the discount's `share` quantile
      (a bad month for the discount alone). Returns [(label, change)]."""
    calm = factors[factors["wti"].abs() < flat]
    worst = calm["discount"].idxmin()
    return [(f"{worst.strftime('%B %Y')}: WTI {100 * calm.at[worst, 'wti']:+.1f}%, the discount's "
             "worst month with WTI flat", float(calm.at[worst, "discount"])),
            (f"the discount's worst {100 * share:.0f}% of months since "
             f"{factors.index[0].strftime('%Y')}", float(factors["discount"].quantile(share)))]
