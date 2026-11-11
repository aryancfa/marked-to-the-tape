"""
references.py — what each implied vol is compared WITH (build sheet B3).

Inverting a price gives one number. A disagreement needs two. This module
supplies the second number three ways, weakest first:

  call minus put      the call and the put at the same strike and expiry
                      should imply the same vol. A gap means a carry input
                      (share price, rate, dividend) is off.
  strike minus smile  each strike against a smooth curve fitted through the
                      OTHER strikes of its expiry. A gap means that one
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
from typing import Sequence

import numpy as np
import pandas as pd

from surface import forward_sensitivity

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
    coef: tuple[float, ...]     # iv = c0 + c1*k + c2*k^2 + ..., k = ln(strike/share price)
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
# one day: every difference against every reference
# --------------------------------------------------------------------------

def reference_differences(inverted: pd.DataFrame, spot: float) -> pd.DataFrame:
    """
    One row per difference, from one day's surface.invert_chain output.

    Columns: date, expiry, days, strike, moneyness (strike / share price),
    reference, side ("C", "P", or "pair" for call minus put), diff (VOL
    POINTS), quote_bar (vol points; a pair's is its two quotes' bars added),
    tier (a pair takes the weaker of its two), poor_fit.

    poor_fit marks smile rows from an expiry where the curve itself misses
    by more than the typical quote bar. There the quadratic is the wrong
    shape, and a residual says so about the curve, not about the quote.
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
    for _, e in ok.groupby("expiry"):
        e = e[np.isfinite(e["vega"]) & (e["vega"] > 0)]
        fit = fit_smile(np.log(e["moneyness"]), e["iv"], e["vega"]) if len(e) else None
        if fit is None:
            continue
        poor = bool(fit.rms * 100.0 > e["quote_bar"].median())
        out.append(e[base].assign(
            reference=SMILE, side=e["call_put"], diff=fit.residual * 100.0,
            quote_bar=e["quote_bar"], tier=e["tier"], poor_fit=poor))

    # 3. ours minus the exchange's published vol
    x = ok[ok["mx_iv"].notna()] if "mx_iv" in ok else ok.iloc[0:0]
    if len(x):
        out.append(x[base].assign(
            reference=EXCHANGE, side=x["call_put"], diff=(x["iv"] - x["mx_iv"]) * 100.0,
            quote_bar=x["quote_bar"], tier=x["tier"], poor_fit=False))

    cols = base + ["reference", "side", "diff", "quote_bar", "tier", "poor_fit"]
    if not out:
        return pd.DataFrame(columns=cols)
    d = pd.concat(out, ignore_index=True)[cols]
    return d[d["diff"].notna()].reset_index(drop=True)


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
      std_error       day-to-day scatter / sqrt(days)
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
    g = per_day.groupby(keys, observed=True)
    out = pd.DataFrame({"days": g["diff"].count(), "points": g["points"].sum(),
                        "mean": g["diff"].mean(), "sd": g["diff"].std(),
                        "mean_quote_bar": g["bar"].mean()})
    out["std_error"] = out["sd"] / np.sqrt(out["days"])
    out["t_stat"] = out["mean"] / out["std_error"].where(out["std_error"] > 0)
    out["in_quote_bars"] = out["mean"] / out["mean_quote_bar"]
    out["disagreement"] = (out["days"] >= min_days) & (out["t_stat"].abs() > 2.0)
    return out.drop(columns="sd").reset_index()[
        keys + ["days", "points", "mean", "std_error", "t_stat", "mean_quote_bar",
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
