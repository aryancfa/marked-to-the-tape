"""
references.py — what each implied vol is compared WITH (build sheet B3).

Inverting a price gives one number. A disagreement needs two. This module
supplies the second number three ways, weakest first:

  call minus put      the call and the put at the same strike and expiry
                      should imply the same vol. A gap means a carry input
                      (share price, rate, dividend) is off.
  strike minus smile  each strike against an arbitrage-free curve (SVI, in
                      strike over forward) fitted through the OTHER strikes
                      of its expiry. A gap means that one
                      quote is out of line with its neighbours.
  ours minus exchange our vol against the vol the exchange publishes in the
                      same file. A gap means the two models differ.

The fourth reference, OptionMetrics on the New York listings, is B8.

Every difference is in VOL POINTS and carries its quote bar (the half-spread
in vol points), so it can be read two ways: against its standard error
across days, which says whether it is real, and against the quote bar, which
says whether anyone could trade on it. It computes; it does not read files
or draw charts.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Sequence

import numpy as np
import pandas as pd

from surface import dividend_times, forward_sensitivity, mean_std_error
from tradingcal import TSX
from voleng import pv_dividends

PAIR, SMILE, EXCHANGE = "call minus put", "strike minus smile", "ours minus exchange"

# bucket edges: strike / share price, and calendar days to expiry
M_EDGES = (0.88, 0.95, 0.98, 1.02, 1.05, 1.12)
D_EDGES = (6, 30, 60, 120, 200, 400)

_TIER_RANK = {"traded": 0, "quoted": 1, "settlement_only": 2}


# --------------------------------------------------------------------------
# the smile
# --------------------------------------------------------------------------

@dataclass
class SmileFit:
    coef: tuple[float, ...]     # cubic: c0, c1, c2, c3 in k = ln(strike/share price);
                                # SVI: a, b, rho, m, s in k = ln(strike/forward)
    residual: np.ndarray        # iv minus the curve fitted WITHOUT that strike
    rms: float                  # weighted root-mean-square of those residuals
    n: int


def _wls(k: np.ndarray, iv: np.ndarray, w: np.ndarray, degree: int) -> np.ndarray:
    X = np.vander(k, degree + 1, increasing=True)
    sw = np.sqrt(w)
    coef, *_ = np.linalg.lstsq(X * sw[:, None], iv * sw, rcond=None)
    return coef


def _curve(coef: np.ndarray, k: np.ndarray) -> np.ndarray:
    return np.vander(k, len(coef), increasing=True) @ coef


def fit_smile(k: Sequence[float], iv: Sequence[float], w: Sequence[float],
              degree: int = 3, min_strikes: int | None = None) -> SmileFit | None:
    """
    Fit a polynomial smile by weighted least squares in k = ln(strike / share
    price). Returns None with fewer than `min_strikes` distinct strikes
    (default degree + 2): a cubic passes through any four points, so with no
    strike to spare the fit tests nothing.

    Why a cubic. The build sheet started with a quadratic. On RY the
    quadratic's misses came back the same way every day, in a mirror-image
    pattern across the money: low, high, zero, low, high. That is the
    footprint of a smile that is lopsided, steeper on the put side than on
    the call side, being forced onto a symmetric bowl. The cubic term is the
    tilt. Without it the pooled chart reported the curve's own shape error as
    disagreements.

    Weights are vega. Vega is how many dollars one vol point is worth, so a
    high-vega quote pins the vol down tightly and a low-vega one hardly at
    all. Weighting by it lets the well-determined points set the curve.

    `residual` is each point's vol minus a curve fitted without ANY point at
    its strike (the call and the put there are both left out). A curve
    fitted through a point bends toward it and understates how far out of
    line it is. Leaving the strike out asks the honest question: given its
    neighbours, where should this strike be? The cost is at the two end
    strikes, which are then judged by a curve extended past its data.
    """
    k, iv, w = (np.asarray(x, dtype=float) for x in (k, iv, w))
    need = degree + 2 if min_strikes is None else min_strikes
    strikes = np.unique(np.round(k, 10))
    if len(strikes) < need:
        return None
    coef = _wls(k, iv, w, degree)
    residual = np.full(len(k), np.nan)
    for s_ in strikes:
        here = np.isclose(k, s_, atol=1e-10)
        if len(np.unique(np.round(k[~here], 10))) < degree + 1:
            continue
        residual[here] = iv[here] - _curve(_wls(k[~here], iv[~here], w[~here], degree), k[here])
    ok = np.isfinite(residual)
    rms = float(np.sqrt(np.sum(w[ok] * residual[ok] ** 2) / np.sum(w[ok]))) if ok.any() else np.nan
    return SmileFit(tuple(float(c) for c in coef), residual, rms, int(len(k)))


# --------------------------------------------------------------------------
# the smile a desk would use: SVI, against the forward
# --------------------------------------------------------------------------

SVI_MIN_STRIKES = 7         # five numbers to fit, two strikes to spare


def svi_total_variance(params: Sequence[float], k) -> np.ndarray:
    """w(k) = a + b * (rho * (k - m) + sqrt((k - m)^2 + s^2)), where k is
    ln(strike / forward) and w is implied vol squared times time to expiry."""
    a, b, rho, m, s_ = params
    k = np.asarray(k, dtype=float)
    return a + b * (rho * (k - m) + np.sqrt((k - m) ** 2 + s_ * s_))


def svi_butterfly_free(params: Sequence[float], k_lo: float, k_hi: float,
                       points: int = 201) -> bool:
    """
    True if the curve implies no negative probability between k_lo and k_hi.

    A smile is a set of option prices, and option prices imply how likely
    each future share price is. A curve that bends the wrong way implies a
    NEGATIVE likelihood somewhere: a butterfly spread there would cost less
    than nothing. The test is Gatheral's function g(k), which is that
    likelihood up to a positive factor, and it must stay at or above zero.
    """
    a, b, rho, m, s_ = params
    k = np.linspace(k_lo, k_hi, points)
    root = np.sqrt((k - m) ** 2 + s_ * s_)
    w = a + b * (rho * (k - m) + root)
    if (w <= 0).any():
        return False
    w1 = b * (rho + (k - m) / root)
    w2 = b * s_ * s_ / root ** 3
    g = (1.0 - k * w1 / (2.0 * w)) ** 2 - 0.25 * w1 * w1 * (1.0 / w + 0.25) + 0.5 * w2
    return bool((g >= -1e-10).all())


def _svi_best(k, w, wt, m_grid, s_grid):
    """For every (m, s) on the grid the other three numbers come from one
    weighted least-squares solve, because with m and s fixed the curve is a
    straight-line combination of 1, y and sqrt(y^2 + 1), y = (k - m) / s.
    Returns the best-fitting candidate that respects the wing limits and
    admits no butterfly arbitrage, or None."""
    M, S_ = (g.ravel() for g in np.meshgrid(m_grid, s_grid, indexing="ij"))
    y = (k[None, :] - M[:, None]) / S_[:, None]
    X = np.stack([np.ones_like(y), y, np.sqrt(y * y + 1.0)], axis=2)
    XtW = X.transpose(0, 2, 1) * wt[None, None, :]
    A = XtW @ X
    A += 1e-12 * np.trace(A, axis1=1, axis2=2)[:, None, None] * np.eye(3)[None]
    sol = np.linalg.solve(A, (XtW @ w)[:, :, None])[:, :, 0]
    a, d, c = sol[:, 0], sol[:, 1], np.maximum(sol[:, 2], 0.0)
    sse = (wt[None, :] * (np.einsum("gnj,gj->gn", X, sol) - w[None, :]) ** 2).sum(axis=1)
    # c = b*s and d = rho*b*s. Limits: b >= 0, |rho| <= 1, each wing's slope
    # at most 2 in total variance (beyond that the far wing itself is an
    # arbitrage), and the lowest point of the curve not below zero.
    tol = 1e-12
    ok = ((sol[:, 2] >= -tol) & (np.abs(d) <= c + tol) & (c + np.abs(d) <= 2.0 * S_ + tol)
          & (a + np.sqrt(np.maximum(c * c - d * d, 0.0)) >= -tol) & np.isfinite(sse))
    b = c / S_
    rho = np.clip(np.divide(d, c, out=np.zeros_like(d), where=c > 0), -1.0, 1.0)
    # no butterfly arbitrage, checked well past the last strike on each side
    # (the same test as svi_butterfly_free, for every candidate at once)
    kk = np.linspace(k.min() - 0.5, k.max() + 0.5, 121)[None, :]
    root = np.sqrt((kk - M[:, None]) ** 2 + (S_ * S_)[:, None])
    ww = a[:, None] + b[:, None] * (rho[:, None] * (kk - M[:, None]) + root)
    w1 = b[:, None] * (rho[:, None] + (kk - M[:, None]) / root)
    w2 = b[:, None] * (S_ * S_)[:, None] / root ** 3
    with np.errstate(divide="ignore", invalid="ignore"):
        g = (1.0 - kk * w1 / (2.0 * ww)) ** 2 - 0.25 * w1 * w1 * (1.0 / ww + 0.25) + 0.5 * w2
    ok &= (ww > 0).all(axis=1) & (np.nan_to_num(g, nan=-1.0) >= -1e-10).all(axis=1)
    if not ok.any():
        return None
    i = int(np.flatnonzero(ok)[np.argmin(sse[ok])])
    return (float(a[i]), float(b[i]), float(rho[i]), float(M[i]), float(S_[i]))


def _svi_slice(k, w, wt, start=None):
    """Best arbitrage-free SVI curve through (k, w), or None. A coarse grid
    over (m, s), then two finer ones around the best point. `start` skips
    the coarse pass and searches around a known (m, s)."""
    span = max(k.max() - k.min(), 0.02)
    if start is None:
        m_grid = np.linspace(k.min() - 0.5 * span, k.max() + 0.5 * span, 25)
        s_grid = np.geomspace(0.005, 1.0, 20)
    else:
        m_grid = start[0] + np.linspace(-0.25, 0.25, 15) * span
        s_grid = start[1] * np.geomspace(0.5, 2.0, 11)
    best = _svi_best(k, w, wt, m_grid, s_grid)
    for shrink in (0.12, 0.03):
        if best is None:
            return None
        m_grid = best[3] + np.linspace(-shrink, shrink, 13) * span
        s_grid = best[4] * np.geomspace(1.0 - 2.0 * shrink, 1.0 + 2.5 * shrink, 11)
        best = _svi_best(k, w, wt, m_grid, s_grid) or best
    return best


def fit_svi(k: Sequence[float], iv: Sequence[float], w: Sequence[float], T: float,
            min_strikes: int = SVI_MIN_STRIKES) -> SmileFit | None:
    """
    Fit an SVI smile to one expiry. `k` is ln(strike / FORWARD), `iv` the
    implied vols, `w` the weights (vega), `T` years to expiry. Returns None
    with fewer than `min_strikes` distinct strikes, or if no curve free of
    butterfly arbitrage fits. coef is (a, b, rho, m, s).

    Why this and not a polynomial. A cubic is only a flexible line: nothing
    stops it implying prices no market could show, and extended past its
    last strike it runs off. SVI is the form desks use because its five
    numbers mean something (level, wing steepness, tilt, where the bottom
    sits, how rounded the bottom is), its wings go straight, which is what
    theory says far wings must do, and it can be held to shapes that admit
    no butterfly arbitrage.

    Why the forward. Where the smile's bottom sits is a statement about the
    price the share is expected to be delivered at on expiry, which is the
    forward: the share price, less the dividends paid before then, grown at
    the funding rate. Measured from today's share price instead, every
    expiry's smile is shifted sideways by its own carry.

    It is fitted in total variance (vol squared times T), where the form is
    defined. A weight on a vol error becomes a weight on a variance error
    through d(variance) = 2 * vol * T * d(vol).

    `residual` is, as before, each point's vol minus the curve fitted
    WITHOUT any point at its strike.

    One expiry at a time. Each curve is free of arbitrage ACROSS STRIKES.
    Nothing here ties expiries together, so this is a set of slices, not a
    surface: it does not check that total variance never falls with time.
    """
    k, iv, w = (np.asarray(x, dtype=float) for x in (k, iv, w))
    strikes = np.unique(np.round(k, 10))
    if len(strikes) < min_strikes or T <= 0:
        return None
    tv = iv * iv * T
    wt = w / np.maximum(2.0 * iv * T, 1e-12) ** 2
    full = _svi_slice(k, tv, wt)
    if full is None:
        return None
    residual = np.full(len(k), np.nan)
    for s_ in strikes:
        here = np.isclose(k, s_, atol=1e-10)
        if len(np.unique(np.round(k[~here], 10))) < min_strikes - 1:
            continue
        loo = _svi_slice(k[~here], tv[~here], wt[~here], start=(full[3], full[4]))
        if loo is None:
            continue
        residual[here] = iv[here] - np.sqrt(np.maximum(svi_total_variance(loo, k[here]), 0.0) / T)
    ok = np.isfinite(residual)
    rms = float(np.sqrt(np.sum(w[ok] * residual[ok] ** 2) / np.sum(w[ok]))) if ok.any() else np.nan
    return SmileFit(full, residual, rms, int(len(k)))


def forward_price(spot: float, T: float, r: float,
                  divs: Sequence[tuple[float, float]]) -> float:
    """The share price for delivery at T: today's price less the present
    value of the cash dividends paid before then, grown at the rate r.
    `divs` is [(years from now, cash)]."""
    return (spot - pv_dividends(divs, r, 0.0, T)) * float(np.exp(r * T))


# --------------------------------------------------------------------------
# all expiries at once: a surface (SSVI), and the check across time
# --------------------------------------------------------------------------

@dataclass
class SurfaceFit:
    rho: float                  # tilt, the same at every expiry
    eta: float                  # how strongly the smile curves
    gamma: float                # how fast that curvature fades with maturity
    theta: tuple[float, ...]    # at-the-money total variance per expiry, in time order
    raised: int                 # expiries whose theta had to be lifted to keep it rising
    rms: float                  # weighted root-mean-square miss, in vol units
    n: int


def ssvi_total_variance(theta, k, rho: float, eta: float, gamma: float) -> np.ndarray:
    """Gatheral and Jacquier's surface. `theta` is the at-the-money total
    variance of the expiry (vol squared times time), k is ln(strike / forward):

        w = theta / 2 * (1 + rho*phi*k + sqrt((phi*k + rho)^2 + 1 - rho^2)),
        phi = eta * theta^(-gamma)

    With 0 < gamma <= 1/2, eta * (1 + |rho|) <= 2 and theta rising with
    maturity, the surface has no butterfly arbitrage at any expiry and no
    calendar arbitrage between expiries."""
    theta = np.asarray(theta, dtype=float)
    k = np.asarray(k, dtype=float)
    phi = eta * theta ** (-gamma)
    return 0.5 * theta * (1.0 + rho * phi * k + np.sqrt((phi * k + rho) ** 2 + 1.0 - rho * rho))


def atm_total_variance(k, iv, w, T: float) -> float:
    """Total variance at the forward (k = 0): calls and puts at a strike are
    averaged by weight, then read off a straight line between the strikes
    either side of the forward. NaN if the strikes do not straddle it."""
    k, iv, w = (np.asarray(x, dtype=float) for x in (k, iv, w))
    ks = np.unique(np.round(k, 10))
    tv = np.array([np.average(iv[np.isclose(k, x, atol=1e-10)] ** 2 * T,
                              weights=w[np.isclose(k, x, atol=1e-10)]) for x in ks])
    if len(ks) < 2 or ks[0] > 0.0 or ks[-1] < 0.0:
        return float("nan")
    return float(np.interp(0.0, ks, tv))


def fit_ssvi(slices: Sequence[tuple], min_points: int = 8) -> SurfaceFit | None:
    """
    Fit one surface to every expiry of a day. `slices` is a list of
    (k, iv, weight, T), one per expiry, in time order, k = ln(strike / forward).

    Each expiry's level is taken from the data: its at-the-money total
    variance (atm_total_variance). Where that would FALL from one expiry to
    the next it is lifted to the earlier level and counted in `raised`,
    because total variance that falls with time is a calendar arbitrage: you
    could sell the nearer option, buy the further one, and never lose.

    Three numbers are then shared by every expiry (rho, eta, gamma), found by
    a grid search held inside the no-arbitrage region and refined once. With
    only three shared numbers, an expiry with too few strikes for a curve of
    its own still gets one, shaped by all the others.
    """
    use = [(np.asarray(k, float), np.asarray(iv, float), np.asarray(w, float), float(T))
           for k, iv, w, T in slices]
    theta = np.array([atm_total_variance(*x) for x in use])
    if not np.isfinite(theta).all() or len(use) < 2 or sum(len(x[0]) for x in use) < min_points:
        return None
    lifted = np.maximum.accumulate(theta)
    raised = int((lifted > theta + 1e-15).sum())
    K = np.concatenate([x[0] for x in use])
    V = np.concatenate([x[1] for x in use])
    TT = np.concatenate([np.full(len(x[0]), x[3]) for x in use])
    TH = np.concatenate([np.full(len(x[0]), th) for x, th in zip(use, lifted)])
    W = np.concatenate([x[2] for x in use])
    target, wt = V * V * TT, W / np.maximum(2.0 * V * TT, 1e-12) ** 2

    def search(rhos, gammas, shares):
        best = (np.inf, None)
        for g in gammas:
            for rho in rhos:
                for sh in shares:                   # eta as a share of its ceiling
                    eta = sh * 2.0 / (1.0 + abs(rho))
                    miss = ssvi_total_variance(TH, K, rho, eta, g) - target
                    sse = float(wt @ (miss * miss))
                    if sse < best[0]:
                        best = (sse, (float(rho), float(eta), float(g)))
        return best[1]

    rho, eta, g = search(np.linspace(-0.95, 0.95, 39), (0.1, 0.2, 0.3, 0.4, 0.5),
                         np.linspace(0.02, 1.0, 25))
    sh = eta * (1.0 + abs(rho)) / 2.0
    rho, eta, g = search(np.clip(rho + np.linspace(-0.05, 0.05, 11), -0.99, 0.99),
                         np.clip(g + np.linspace(-0.1, 0.1, 5), 0.05, 0.5),
                         np.clip(sh + np.linspace(-0.04, 0.04, 9), 0.005, 1.0))
    model = np.sqrt(ssvi_total_variance(TH, K, rho, eta, g) / TT)
    rms = float(np.sqrt(np.sum(W * (V - model) ** 2) / np.sum(W)))
    return SurfaceFit(rho, eta, g, tuple(float(x) for x in lifted), raised, rms, int(len(K)))


def calendar_violations(curves: Sequence[tuple], floor_vol_points: float = 0.04) -> tuple[int, int]:
    """
    (pairs of neighbouring expiries tested, pairs where the later one's
    total variance sits BELOW the earlier one's somewhere on the strikes they
    share). `curves` is a list of (T, SVI params, k_low, k_high) in time order.

    A shortfall smaller than `floor_vol_points`, expressed as vol on the
    later expiry, is the solver's rounding and is not counted.
    """
    tested = bad = 0
    for (T1, p1, lo1, hi1), (T2, p2, lo2, hi2) in zip(curves, curves[1:]):
        lo, hi = max(lo1, lo2), min(hi1, hi2)
        if hi <= lo:
            continue
        k = np.linspace(lo, hi, 41)
        w1, w2 = svi_total_variance(p1, k), svi_total_variance(p2, k)
        short = (np.sqrt(np.maximum(w1, 0.0) / T2) - np.sqrt(np.maximum(w2, 0.0) / T2)) * 100.0
        tested += 1
        bad += int(short.max() > floor_vol_points)
    return tested, bad


# --------------------------------------------------------------------------
# one day: every difference against every reference
# --------------------------------------------------------------------------

def reference_differences(inverted: pd.DataFrame, spot: float,
                          published: str = "mx_iv",
                          divs: Sequence[tuple[date, float]] = (),
                          venue: str = TSX, smile: str = "svi") -> pd.DataFrame:
    """
    One row per difference, from one day's surface.invert_chain output.

    Columns: date, expiry, days, strike, moneyness (strike / share price),
    reference, side ("C", "P", or "pair" for call minus put), diff (VOL
    POINTS), quote_bar (vol points; a pair's is its two quotes' bars added),
    tier (a pair takes the weaker of its two), poor_fit.

    `published` names the column holding someone else's implied vol for the
    third reference: the exchange's (mx_iv) or OptionMetrics' (om_iv).

    The smile is SVI in ln(strike / forward) (fit_svi). `divs` is the dated
    dividend schedule, [(ex-date, cash)], and `venue` the exchange whose
    sessions place them; with the row's own rate they give each expiry's
    forward. `smile="cubic"` uses the older polynomial in ln(strike / share
    price) instead (fit_smile), kept for comparison.

    An expiry with at least seven strikes is judged by its own curve
    (smile_model "slice"). One with fewer is judged by the day's SURFACE
    (fit_ssvi, smile_model "surface"), which borrows its shape from every
    other expiry; there the strike is left out of that expiry's level but
    not of the three shared numbers, which a single strike barely moves.
    An expiry that gets neither is counted, not dropped silently.

    poor_fit marks smile rows from an expiry where the curve itself misses
    by more than the typical quote bar. There the curve is the wrong shape,
    and a residual says so about the curve, not about the quote.

    The returned frame carries a count in `.attrs["smile"]`: expiries seen,
    fitted by slice, fitted by surface, not fitted, neighbouring expiry
    pairs tested for calendar arbitrage and pairs that fail, and expiries
    whose level the surface had to lift. pandas drops attrs on concat, so
    read it before pooling days (smile_counts does).
    """
    ok = inverted[inverted["iv"].notna()].copy()
    ok["expiry"] = [e.date() if hasattr(e, "date") else e for e in ok["expiry"]]
    day = ok["date"].iloc[0] if len(ok) else None
    ok["days"] = [(e - day.date()).days for e in ok["expiry"]] if len(ok) else []
    ok["moneyness"] = ok["strike"] / spot
    ok["quote_bar"] = ok["half_spread_vol"] * 100.0
    base = ["date", "expiry", "days", "strike", "moneyness"]
    out = []

    # 1. call minus put at the same strike
    c, p = ok[ok["call_put"] == "C"], ok[ok["call_put"] == "P"]
    m = c.merge(p, on=base, suffixes=("_c", "_p"))
    if len(m):
        tier = np.where(m["tier_c"].map(_TIER_RANK) >= m["tier_p"].map(_TIER_RANK),
                        m["tier_c"], m["tier_p"])
        out.append(m[base].assign(
            reference=PAIR, side="pair", diff=(m["iv_c"] - m["iv_p"]) * 100.0,
            quote_bar=m["quote_bar_c"] + m["quote_bar_p"], tier=tier, poor_fit=False))

    # 2. each strike against the smile of its expiry
    count = dict(expiries=0, slice=0, surface=0, unfitted=0, calendar_pairs=0,
                 calendar_violations=0, levels_raised=0)
    groups, curves, thin = [], [], []
    for expiry, e in ok.groupby("expiry"):
        e = e[np.isfinite(e["vega"]) & (e["vega"] > 0)]
        if not len(e):
            continue
        count["expiries"] += 1
        if smile == "cubic":
            fit, k, T = fit_smile(np.log(e["moneyness"]), e["iv"], e["vega"]), None, None
        else:
            T, r = float(e["T"].iloc[0]), float(e["r"].iloc[0])
            fwd = forward_price(spot, T, r, dividend_times(day.date(), divs, expiry,
                                                           venue=venue))
            k = np.log(e["strike"].to_numpy(dtype=float) / fwd)
            fit = fit_svi(k, e["iv"], e["vega"], T)
            groups.append((k, e["iv"].to_numpy(dtype=float), e["vega"].to_numpy(dtype=float), T))
        if fit is None:
            thin.append((len(groups) - 1, e))
            continue
        count["slice"] += 1
        if k is not None:
            curves.append((T, fit.coef, float(k.min()), float(k.max())))
        poor = bool(fit.rms * 100.0 > e["quote_bar"].median())
        out.append(e[base].assign(
            reference=SMILE, side=e["call_put"], diff=fit.residual * 100.0,
            quote_bar=e["quote_bar"], tier=e["tier"], poor_fit=poor, smile_model="slice"))

    if smile != "cubic":
        count["calendar_pairs"], count["calendar_violations"] = calendar_violations(curves)
        surface = fit_ssvi(groups) if thin else None
        if surface is not None:
            count["levels_raised"] = surface.raised
        for i, e in thin:
            k, iv, vega, T = groups[i]
            resid = np.full(len(k), np.nan)
            if surface is not None:
                for x in np.unique(np.round(k, 10)):
                    here = np.isclose(k, x, atol=1e-10)
                    th = atm_total_variance(k[~here], iv[~here], vega[~here], T)
                    if np.isfinite(th):         # this expiry's level without the strike
                        th = max(th, max(surface.theta[:i], default=0.0))
                        resid[here] = iv[here] - np.sqrt(ssvi_total_variance(
                            th, k[here], surface.rho, surface.eta, surface.gamma) / T)
            if not np.isfinite(resid).any():
                count["unfitted"] += 1
                continue
            count["surface"] += 1
            okr = np.isfinite(resid)
            rms = float(np.sqrt(np.sum(vega[okr] * resid[okr] ** 2) / np.sum(vega[okr])))
            out.append(e[base].assign(
                reference=SMILE, side=e["call_put"], diff=resid * 100.0,
                quote_bar=e["quote_bar"], tier=e["tier"],
                poor_fit=bool(rms * 100.0 > e["quote_bar"].median()), smile_model="surface"))
    else:
        count["unfitted"] = len(thin)

    # 3. ours minus the exchange's published vol
    x = ok[ok[published].notna()] if published in ok else ok.iloc[0:0]
    if len(x):
        out.append(x[base].assign(
            reference=EXCHANGE, side=x["call_put"], diff=(x["iv"] - x[published]) * 100.0,
            quote_bar=x["quote_bar"], tier=x["tier"], poor_fit=False))

    cols = base + ["reference", "side", "diff", "quote_bar", "tier", "poor_fit", "smile_model"]
    if not out:
        empty = pd.DataFrame(columns=cols)
        empty.attrs["smile"] = count
        return empty
    d = pd.concat(out, ignore_index=True).reindex(columns=cols)
    d["smile_model"] = d["smile_model"].fillna("")
    d = d[d["diff"].notna()].reset_index(drop=True)
    d.attrs["smile"] = count
    return d


def smile_counts(daily: Sequence[pd.DataFrame]) -> dict:
    """Add up the smile counts carried by each day's reference_differences
    output. Call it BEFORE concatenating the days: pandas drops attrs there."""
    total: dict = {}
    for d in daily:
        for key, v in d.attrs.get("smile", {}).items():
            total[key] = total.get(key, 0) + int(v)
    return total


# --------------------------------------------------------------------------
# across days
# --------------------------------------------------------------------------

def _label(edges: Sequence[float], fmt: str) -> list[str]:
    return [f"{fmt.format(a)}-{fmt.format(b)}" for a, b in zip(edges, edges[1:])]


def add_buckets(diffs: pd.DataFrame, m_edges: Sequence[float] = M_EDGES,
                d_edges: Sequence[float] = D_EDGES) -> pd.DataFrame:
    """Adds moneyness_bucket and days_bucket. Rows outside the edges get an
    empty bucket and drop out of any pooling."""
    d = diffs.copy()
    d["moneyness_bucket"] = pd.cut(d["moneyness"], list(m_edges), labels=_label(m_edges, "{:.2f}"))
    d["days_bucket"] = pd.cut(d["days"], list(d_edges),
                              labels=[f"{int(a) + 1}-{int(b)}d" for a, b in zip(d_edges, d_edges[1:])])
    return d


def tightest(diffs: pd.DataFrame, share: float = 1.0 / 3.0) -> pd.DataFrame:
    """
    The rows with the narrowest quotes: the tightest `share` by quote bar,
    taken within each reference and days bucket (short expiries are quoted
    wider in vol points than long ones, so one cut for all would keep only
    long ones). If a difference shows in the full set but not here, wide
    quotes made it; if it shows here too, it is not a quote artefact.
    """
    d = add_buckets(diffs) if "days_bucket" not in diffs else diffs
    d = d[d["quote_bar"].notna()]
    cut = d.groupby(["reference", "days_bucket"], observed=True)["quote_bar"].transform(
        lambda s: s.quantile(share))
    return d[d["quote_bar"] <= cut]


def pool_buckets(diffs: pd.DataFrame, m_edges: Sequence[float] = M_EDGES,
                 d_edges: Sequence[float] = D_EDGES, min_days: int = 5) -> pd.DataFrame:
    """
    Pool differences across days by reference, side, days bucket and
    moneyness bucket.

      days            days with at least one point in the bucket
      points          differences behind it
      mean            mean across days of each day's own mean (vol points)
      std_error       of the mean, allowing a day to resemble the days before
                      it (surface.mean_std_error, Newey-West)
      std_error_iid   day-to-day scatter / sqrt(days): the plain formula,
                      which assumes independent days
      t_stat          mean / std_error
      mean_quote_bar  the typical quote bar in the bucket
      in_quote_bars   mean / mean_quote_bar: above 1 would be tradeable
      disagreement    |t| above 2 on at least `min_days` days

    Each day is averaged first so that a day with thirty strikes does not
    outvote a day with three. Quote noise changes from day to day and
    averages away; a real difference comes back every day. So a mean more
    than two standard errors from zero, seen on enough days, is called a
    disagreement, and nothing else is. Smile rows from poor fits are left out.
    """
    d = add_buckets(diffs, m_edges, d_edges)
    if "poor_fit" in d:
        d = d[~d["poor_fit"].astype(bool)]
    keys = ["reference", "side", "days_bucket", "moneyness_bucket"]
    d = d.dropna(subset=["days_bucket", "moneyness_bucket"])
    per_day = d.groupby(keys + ["date"], observed=True).agg(
        diff=("diff", "mean"), bar=("quote_bar", "mean"), points=("diff", "size"))
    g = per_day.sort_index(level="date").groupby(keys, observed=True)
    out = pd.DataFrame({"days": g["diff"].count(), "points": g["points"].sum(),
                        "mean": g["diff"].mean(), "sd": g["diff"].std(),
                        "mean_quote_bar": g["bar"].mean(),
                        "std_error": g["diff"].apply(mean_std_error)})
    out["std_error_iid"] = out["sd"] / np.sqrt(out["days"])
    out["t_stat"] = out["mean"] / out["std_error"].where(out["std_error"] > 0)
    out["in_quote_bars"] = out["mean"] / out["mean_quote_bar"]
    out["disagreement"] = (out["days"] >= min_days) & (out["t_stat"].abs() > 2.0)
    return out.drop(columns="sd").reset_index()[
        keys + ["days", "points", "mean", "std_error", "std_error_iid", "t_stat",
                "mean_quote_bar",
                "in_quote_bars", "disagreement"]]


# --------------------------------------------------------------------------
# how far a wrong input moves the call-put gap
# --------------------------------------------------------------------------

def sensitivity_table(S: float, sigma: float, r: float, dividend: float,
                      days: Sequence[int] = (30, 90, 180, 365), n: int = 400) -> pd.DataFrame:
    """
    Vol points of call-minus-put gap, at the money, produced by three stated
    input errors, by days to expiry. `dividend` is one cash dividend, placed
    half way to expiry.

      spot 10 bp low    the share price used is 0.10% below the true one
      rate 25 bp low    the rate used is 0.25% a year below the true one
      dividend 5% high  the dividend used is 5% above the true one

    Each makes the model's forward price too low, so calls look dear and the
    gap is positive. A spot error is the same forward error at every
    maturity, and vol is less sensitive to it the longer the option, so it
    fades. A rate error is a forward error that grows with time, so it grows.
    """
    rows = {}
    for d in days:
        T = d / 365.0
        sens = forward_sensitivity(S, S, T, r, sigma, [(T / 2.0, dividend)], n)
        rows[d] = {"spot 10 bp low": 10.0 * sens,
                   "rate 25 bp low": 25.0 * T * sens,
                   "dividend 5% high": 0.05 * dividend / S * 1e4 * sens}
    out = pd.DataFrame.from_dict(rows, orient="index")
    out.index.name = "days"
    return out
