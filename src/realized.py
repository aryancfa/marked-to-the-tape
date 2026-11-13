"""
realized.py — implied vol against the vol that followed (B9).

Implied vol is an option's price quoted as a volatility. Realized vol is how
much the share then moved. The comparison has to look FORWARD: implied on
day t against realized from t to expiry. Setting implied beside the trailing
realized is the usual mistake, because it matches a forecast with the past.

Nothing here reads a file. Vols are decimals (0.20 = 20%) unless a name says
points.
"""

from __future__ import annotations

from math import erf, log, sqrt
from typing import Sequence

import numpy as np


def realized_vol(log_returns: Sequence[float], T: float) -> float:
    """Annualised vol of the moves that happened over a window of T years:
    the square root of the summed squared log returns over T.

    T is in the SAME clock the implied vol uses (calendar days / 365, hard
    rule 5), so the two are comparable: an implied vol of 20% over a window
    says the squared moves should sum to 0.04 * T. The mean return is not
    subtracted; over a month it cannot be told from zero and subtracting it
    throws away a degree of freedom."""
    r = np.asarray(log_returns, dtype=float)
    if T <= 0 or len(r) == 0 or not np.isfinite(r).all():
        return float("nan")
    return float(np.sqrt(np.sum(r * r) / T))


def by_strike(strikes: Sequence[float], ivs: Sequence[float]) -> tuple[np.ndarray, np.ndarray]:
    """One vol per strike (calls and puts at a strike averaged), by strike."""
    k, v = np.asarray(strikes, dtype=float), np.asarray(ivs, dtype=float)
    ok = np.isfinite(k) & np.isfinite(v)
    ks = np.unique(k[ok])
    return ks, np.array([v[ok][k[ok] == x].mean() for x in ks])


def atm_vol(strikes: Sequence[float], ivs: Sequence[float], spot: float) -> float | None:
    """Implied vol at the share price, read between the two strikes around
    it. None when the share price is outside the strikes on file: a vol is
    never carried past the last strike (hard rule 4)."""
    ks, vs = by_strike(strikes, ivs)
    if len(ks) < 2 or not (ks[0] <= spot <= ks[-1]):
        return None
    return float(np.interp(spot, ks, vs))


def _phi(x: float) -> float:
    return 0.5 * (1.0 + erf(x / sqrt(2.0)))


def bs_delta(forward: float, strike: float, T: float, sigma: float, is_call: bool) -> float:
    """Black-Scholes forward delta. Used only to LOCATE the 25-delta strikes;
    the options are American and their vols come from the tree."""
    d1 = (log(forward / strike) + 0.5 * sigma * sigma * T) / (sigma * sqrt(T))
    return _phi(d1) if is_call else _phi(d1) - 1.0


def vol_at_delta(strikes: Sequence[float], ivs: Sequence[float], forward: float, T: float,
                 delta: float) -> float | None:
    """The vol of the option whose delta is `delta` (+0.25 for the 25-delta
    call, -0.25 for the 25-delta put), read between the two strikes that
    bracket it. None when no pair of strikes on file brackets that delta."""
    ks, vs = by_strike(strikes, ivs)
    if len(ks) < 2 or T <= 0:
        return None
    d = np.array([bs_delta(forward, k, T, v, delta > 0) for k, v in zip(ks, vs)])
    order = np.argsort(d)
    d, vs = d[order], vs[order]
    if not (d[0] <= delta <= d[-1]):
        return None
    return float(np.interp(delta, d, vs))


def risk_reversal_butterfly(strikes: Sequence[float], ivs: Sequence[float], forward: float,
                            T: float, spot: float) -> tuple[float, float] | None:
    """25-delta risk reversal and butterfly, in VOL POINTS.

      risk reversal  25-delta call vol minus 25-delta put vol. Negative on
                     shares: downside protection costs more than upside.
      butterfly      the average of the two wings minus the at-the-money vol:
                     how much dearer the wings are than the middle.

    None if either wing or the at-the-money vol cannot be read from the
    strikes on file."""
    call, put = (vol_at_delta(strikes, ivs, forward, T, d) for d in (0.25, -0.25))
    mid = atm_vol(strikes, ivs, spot)
    if call is None or put is None or mid is None:
        return None
    return (call - put) * 100.0, (0.5 * (call + put) - mid) * 100.0


def hedge_pnl(deltas: Sequence[float], spots: Sequence[float],
              dividends: Sequence[float] | None = None) -> float:
    """What a daily delta hedge earned per share of option, holding minus the
    option's delta in shares from each close to the next.

    deltas[i] is the option's delta at close i, spots[i] the share price at
    close i, dividends[i] the cash going ex between close i-1 and close i.
    The hedger is short delta shares, so pays the dividend on them."""
    s, d = np.asarray(spots, dtype=float), np.asarray(deltas, dtype=float)
    div = np.zeros(len(s)) if dividends is None else np.asarray(dividends, dtype=float)
    if len(s) != len(d) or len(s) != len(div):
        raise ValueError("deltas, spots and dividends must be the same length")
    return float(np.sum(-d[:-1] * (s[1:] - s[:-1] + div[1:])))
