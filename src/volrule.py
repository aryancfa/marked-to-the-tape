"""
volrule.py — what implied vol does when the share price moves (C1).

A 20% fall in a share with its implied vol unchanged has never happened.
Shocking the share and holding vol flat is the largest single error in a
stress test, so the vol has to be given a rule. Four are offered, because
desks disagree about which is right and the answer differs by market state:

  sticky strike     each option keeps its own vol. The smile stays put and
                    the share slides along it. Vol at the money changes only
                    by where the share now sits on the old curve.
  sticky moneyness  the smile travels with the share: an option's vol is the
                    old vol at the same strike-to-share ratio. At-the-money
                    vol does not change at all.
  sticky delta      the smile travels with the share measured in delta, not
                    in price ratio. Close to sticky moneyness for small moves.
  beta              at-the-money vol moves by `beta` vol points for each 1%
                    the share moves (negative on shares: down means vol up),
                    on top of sticky moneyness. In a crash (a fall beyond
                    `crash`) the skew also steepens by `steepen`: low strikes
                    gain more vol than high ones.

Only "beta" raises vol in a fall. The first three are what a pricing system
does by default; the fourth is what happens.

A vol produced by any of these is a number someone chose, not one a market
showed. Everything priced with it is a model price and is labelled so (hard
rule 6).

Nothing here reads a file. Vols are decimals.
"""

from __future__ import annotations

from math import log, sqrt
from typing import Callable, Sequence

import numpy as np

STICKY_STRIKE, STICKY_MONEYNESS, STICKY_DELTA, BETA = (
    "sticky strike", "sticky moneyness", "sticky delta", "beta")
RULES = (STICKY_STRIKE, STICKY_MONEYNESS, STICKY_DELTA, BETA)
FLOOR = 0.01                    # a vol is never taken below one point

Smile = Callable[[float], float]


def smile_from_points(strikes: Sequence[float], vols: Sequence[float]) -> Smile:
    """A day's smile for one expiry as a function of strike: straight lines
    between the strikes on file in log strike, and FLAT beyond the last
    strike on each side. Flat is a choice: past the data nothing is known,
    and continuing the slope would invent a wing."""
    k, v = np.asarray(strikes, dtype=float), np.asarray(vols, dtype=float)
    ok = np.isfinite(k) & np.isfinite(v) & (k > 0)
    ks = np.unique(k[ok])
    if len(ks) == 0:
        raise ValueError("no strikes to build a smile from")
    vs = np.array([v[ok][k[ok] == x].mean() for x in ks])
    lk = np.log(ks)
    return lambda strike: float(np.interp(log(strike), lk, vs))


def _d1(spot: float, strike: float, T: float, sigma: float) -> float:
    return (log(spot / strike) + 0.5 * sigma * sigma * T) / (sigma * sqrt(T))


def _same_delta_strike(smile: Smile, strike: float, spot0: float, spot1: float, T: float) -> float:
    """The strike that, BEFORE the move, had the delta `strike` has after it.
    The vol attached to a delta travels with it, so the two share a vol and
    the condition is one equation in the old strike. Solved by bisection."""
    def gap(k0: float) -> float:
        s = max(smile(k0), FLOOR)
        return _d1(spot0, k0, T, s) - _d1(spot1, strike, T, s)
    lo, hi = strike * spot0 / spot1 * 0.5, strike * spot0 / spot1 * 2.0
    if gap(lo) * gap(hi) > 0:
        return strike * spot0 / spot1               # no bracket: fall back to moneyness
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if gap(lo) * gap(mid) <= 0:
            hi = mid
        else:
            lo = mid
    return 0.5 * (lo + hi)


def shocked_vol(rule: str, smile: Smile, strike: float, spot0: float, spot1: float, T: float,
                beta: float = -0.5, crash: float = -0.10, steepen: float = 0.5) -> float:
    """The vol to price `strike` with after the share moves from spot0 to
    spot1, under `rule`. `smile` is the expiry's smile before the move.

    beta     vol points of at-the-money vol per 1% share move (rule "beta")
    crash    the fall beyond which the skew also steepens (rule "beta")
    steepen  how much steeper: 0.5 widens each strike's distance from the
             at-the-money vol by half
    """
    if rule not in RULES:
        raise ValueError(f"unknown vol rule {rule!r}; expected one of {RULES}")
    if min(spot0, spot1, strike) <= 0 or T <= 0:
        raise ValueError("share prices, strike and time must be positive")
    move = spot1 / spot0 - 1.0
    if rule == STICKY_STRIKE:
        vol = smile(strike)
    elif rule == STICKY_MONEYNESS:
        vol = smile(strike * spot0 / spot1)
    elif rule == STICKY_DELTA:
        vol = smile(_same_delta_strike(smile, strike, spot0, spot1, T))
    else:
        atm = smile(spot0)
        wing = smile(strike * spot0 / spot1) - atm          # this strike against the money
        if move <= crash:
            wing *= 1.0 + steepen
        vol = atm + beta * move + wing                      # beta points per 1% = beta per 1.0
    return max(float(vol), FLOOR)


def fitted_beta(returns: Sequence[float], vol_moves_points: Sequence[float]) -> tuple[float, float, int]:
    """Vol points of at-the-money vol per 1% share move, by regression through
    the origin of daily vol moves (in points) on daily returns (in percent).
    Returns (beta, its standard error, days used). A description of the days
    on file, not a forecast: in six calm months there is no crash to fit."""
    x = np.asarray(returns, dtype=float) * 100.0
    y = np.asarray(vol_moves_points, dtype=float)
    ok = np.isfinite(x) & np.isfinite(y)
    x, y = x[ok], y[ok]
    if len(x) < 10 or float(np.sum(x * x)) == 0.0:
        return float("nan"), float("nan"), int(len(x))
    b = float(np.sum(x * y) / np.sum(x * x))
    resid = y - b * x
    se = float(np.sqrt(np.sum(resid ** 2) / (len(x) - 1) / np.sum(x * x)))
    return b, se, int(len(x))
