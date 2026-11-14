"""
limits.py — risk limits and how much of each a book uses (C8).

A limit is a policy choice, not a measurement, so it is made from ONE stated
number and measured move sizes, never typed in per risk:

    LOSS   the most the desk accepts losing on a 1-in-100 day, $50,000. A
           made-up desk's appetite, chosen once for both sample books, the
           same way the books themselves are made up.

Each limit is the exposure that loses LOSS on a 1-in-100 day move of its
own factor, everything else still:

    mapped delta   LOSS / the 99th percentile of the index's absolute daily
                   move, over the 250 sessions before the first day on file.
                   In index dollars (each name's delta times its beta).
    vega, by       LOSS / the 99th percentile of the absolute daily change in
    maturity       the book's at-the-money vols in that maturity bucket, in
                   vol points. Montreal vol history starts in May 2026, so
                   this is measured on the days on file, not before them:
                   the one limit here set with some hindsight.
    ES             LOSS, against the book's one-day 97.5% expected shortfall.
                   For a normal distribution the 97.5% ES and the 99% VaR are
                   the same number, so this is the same appetite read in the
                   tail's average.

USAGE is |exposure| / limit. Under 75% is green, 75 to 100% amber, over 100%
a breach.

The ES each evening is a historical simulation through the book's Greeks:
each of the 250 sessions before, applied to every name at once, through
delta, gamma and vega with vol moved by the C1 beta rule (as the VaR
backtest in scripts/tail_risk.py). Not a full revaluation.

Nothing here reads a file or prices anything.
"""

from __future__ import annotations

from typing import Mapping

import numpy as np
import pandas as pd

from tailrisk import es

LOSS = 50_000.0             # the desk's 1-in-100 day loss tolerance, dollars (stated, made up)
LEVEL = 0.99                # how rare the move each limit is sized on
ES_LEVEL = 0.975
AMBER, RED = 0.75, 1.00     # usage bands


def move_size(changes, level: float = LEVEL) -> float:
    """The `level` quantile of the absolute changes: how big a 1-in-100
    day's move is. nan when there are fewer than 20 changes."""
    x = np.abs(np.asarray(pd.Series(changes).dropna(), dtype=float))
    return float(np.quantile(x, level)) if len(x) >= 20 else float("nan")


def limit_from(move: float, loss: float = LOSS) -> float:
    """The exposure that loses `loss` on a move of size `move`."""
    return loss / move if move > 0 else float("nan")


def usage(exposure: float, limit: float) -> float:
    return abs(exposure) / limit if limit > 0 else float("nan")


def band(u: float) -> str:
    if not np.isfinite(u):
        return "no limit"
    return "breach" if u > RED else "amber" if u >= AMBER else "green"


def mapped_delta(dollars: Mapping[str, float], betas: Mapping[str, float]) -> float:
    """Index dollars: each name's dollar delta times its beta. A name with no
    beta is left out and must be reported by the caller."""
    return float(sum(d * betas[n] for n, d in dollars.items() if n in betas and np.isfinite(betas[n])))


def greek_es(D: Mapping[str, float], G: Mapping[str, float], V: Mapping[str, float],
             window: pd.DataFrame, vol_beta: float = -0.5, level: float = ES_LEVEL) -> float:
    """One-day expected shortfall (a positive loss) of a book whose dollar
    delta, half-gamma dollars and vega per name are D, G, V, under each
    row of `window` (one column of daily returns per name): P&L = D r +
    G r^2 + V (vol_beta x 100 r). A name missing from `window` moves 0."""
    sim = np.zeros(len(window))
    for n in D:
        if n not in window:
            continue
        r = window[n].fillna(0.0).to_numpy(float)
        sim += D[n] * r + G.get(n, 0.0) * r * r + V.get(n, 0.0) * (vol_beta * 100.0 * r)
    return es(sim, level)


def vol_changes_by_bucket(atm: pd.DataFrame, bucket_of) -> pd.DataFrame:
    """Daily changes in at-the-money vol (points), per name and expiry, with
    the maturity bucket each change belongs to on its day. `atm` has day,
    name, expiry, iv (points); `bucket_of(day, expiry)` names the bucket.
    Changes across a gap of more than 5 calendar days are dropped."""
    a = atm.sort_values("day")
    out = []
    for (n, e), g in a.groupby(["name", "expiry"]):
        d = pd.to_datetime(g["day"])
        ch = g["iv"].diff()
        ok = d.diff().dt.days.le(5)
        for day, x in zip(d[ok], ch[ok]):
            out.append(dict(day=day, name=n, bucket=bucket_of(day.date(), e), change=float(x)))
    return pd.DataFrame(out, columns=["day", "name", "bucket", "change"])
