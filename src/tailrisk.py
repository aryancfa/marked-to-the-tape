"""
tailrisk.py — losses on the worst days, split by position, and whether a
VaR number has been keeping its promise (C5, C7).

Three questions:

  How bad is a bad day?        VaR is the loss exceeded on only (1 - level)
                               of days. ES (expected shortfall) is the AVERAGE
                               loss on those days, so it sees how bad the tail
                               is and not only where it starts.
  Which position causes it?    Component ES: each position's own average P&L
                               on the book's worst days. The pieces add up to
                               the book's ES exactly, because ES is the
                               average of the book and the book is the sum of
                               its positions (Euler's allocation).
  Has the VaR been honest?     Count the days the loss went past it. Too many
                               means the VaR is too small. Kupiec's test says
                               whether the count is more than chance; the
                               Basel traffic light is the regulators' version
                               of the same count.

Losses are reported as positive numbers. Nothing here reads a file.
"""

from __future__ import annotations

from math import comb, erfc, log, sqrt

import numpy as np
import pandas as pd


def tail_size(n: int, level: float) -> int:
    """How many of n days make up the worst (1 - level): at least one."""
    return max(1, int(np.floor(n * (1.0 - level) + 1e-9)))


def var(pnl, level: float = 0.99) -> float:
    """Historical VaR: the smallest loss among the worst (1 - level) of days."""
    v = np.sort(np.asarray(pd.Series(pnl).dropna(), dtype=float))
    if len(v) == 0:
        return float("nan")
    return float(-v[tail_size(len(v), level) - 1])


def es(pnl, level: float = 0.975) -> float:
    """Expected shortfall: the average loss on the worst (1 - level) of days."""
    v = np.sort(np.asarray(pd.Series(pnl).dropna(), dtype=float))
    if len(v) == 0:
        return float("nan")
    return float(-v[:tail_size(len(v), level)].mean())


def tail_days(total: pd.Series, level: float = 0.975) -> pd.Index:
    """The scenarios that make up the book's tail."""
    t = total.dropna()
    return t.sort_values().index[:tail_size(len(t), level)]


def component_es(pnl: pd.DataFrame, level: float = 0.975) -> pd.Series:
    """Each position's share of the book's ES: minus its average P&L on the
    BOOK's worst days. `pnl` has one column per position and one row per
    scenario. The components sum to es(pnl.sum(axis=1)) exactly.

    A negative component is a position that makes money on the book's bad
    days: a hedge, measured where it matters."""
    days = tail_days(pnl.sum(axis=1), level)
    return -pnl.loc[days].mean()


def marginal_es(pnl: pd.DataFrame, level: float = 0.975, bump: float = 0.01) -> pd.Series:
    """How much the book's ES rises per unit of each position added, by
    making each position `bump` larger in turn and re-measuring. Close to
    the component, but not the same thing: when a position is made bigger
    the set of worst days can change, and this lets it."""
    base = es(pnl.sum(axis=1), level)
    out = {}
    for c in pnl.columns:
        bigger = pnl.sum(axis=1) + bump * pnl[c]
        out[c] = (es(bigger, level) - base) / bump
    return pd.Series(out)


def kupiec(exceptions: int, days: int, level: float = 0.99) -> tuple[float, float]:
    """Kupiec's proportion-of-failures test. Returns (statistic, p-value).

    If the VaR is right, a loss goes past it on (1 - level) of days. The
    statistic compares how likely the count seen is under that rate with how
    likely it is under the rate actually observed; it is chi-squared with one
    degree of freedom. A p-value under 0.05 says the count is not chance.
    It rejects a VaR that is too small AND one that is too large."""
    if days <= 0 or not 0 <= exceptions <= days:
        raise ValueError("need 0 <= exceptions <= days and days > 0")
    p, x, n = 1.0 - level, exceptions, days

    def loglik(q: float) -> float:
        a = (n - x) * log(1.0 - q) if n - x else 0.0
        b = x * log(q) if x else 0.0
        return a + b
    rate = x / n
    stat = -2.0 * (loglik(p) - (loglik(rate) if 0.0 < rate < 1.0 else 0.0))
    return float(stat), float(erfc(sqrt(max(stat, 0.0) / 2.0)))


def traffic_light(exceptions: int, days: int, level: float = 0.99) -> tuple[str, float]:
    """The Basel zone and the probability behind it.

    Basel defines the zones by how likely it is to see this many exceptions
    OR FEWER from a correct model: green below 95%, yellow from 95% up to
    99.99%, red beyond. On 250 days at 99% that gives the familiar 0 to 4,
    5 to 9, and 10 or more. Stating it as a probability lets it be used on
    a different number of days, which a fixed table cannot."""
    p = 1.0 - level
    cdf = sum(comb(days, k) * p ** k * (1.0 - p) ** (days - k) for k in range(exceptions + 1))
    zone = "green" if cdf < 0.95 else "yellow" if cdf < 0.9999 else "red"
    return zone, float(cdf)
