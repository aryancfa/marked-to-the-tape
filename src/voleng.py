"""
voleng.py — pricing and inversion core for a Canadian listed-options book.

Scope: the piece that has to exist before the settle error surface can be drawn.
Everything here is engine + inversion + quote hygiene. No data adapters, no
plotting, no risk aggregation. numpy only.

Design notes that matter for the desk story
-------------------------------------------
1. Singles and ETFs on MX are AMERICAN. Dividends are DISCRETE CASH, not a
   continuous yield. The tree is built on the "risky" part S* = S - PV(divs)
   (escrowed-dividend construction) and the exercise test uses the *actual*
   stock price S*_node + PV(remaining divs). A continuous-yield approximation
   gets the call exercise region wrong the day before ex-div, which is the
   question you will be asked.

2. Inversion uses BISECTION, not Newton. Vega goes to zero in the wings, and
   Newton diverges there. Price is monotone increasing in sigma, so bisection
   on a bracket is unconditionally convergent. Slower, correct, and it never
   silently returns a number from a failed solve.

3. Every rejected quote returns a REASON. A strike that fails no-arbitrage,
   is crossed, or has no bracket is excluded explicitly and countably. This is
   the difference between an error surface and a plot of garbage: you must be
   able to say how many quotes you dropped and why.

4. Implied borrow is CUT from the product. finance_band_american stays only
   because its tests document why: a one-sided band misses the truth on
   hard-to-borrow names, and the honest two-sided band is too wide to use.

Time convention (the caller's job, stated here so it is done one way)
---------------------------------------------------------------------
Valuation is at the close. Times are calendar days / 365 between dates:
  T   = (expiry date - valuation date) / 365
  t_k = (ex-dividend date - valuation date) / 365
A dividend counts when 0 < t_k <= T. So an ex-date ON the expiry date counts
(the share has already dropped when the option expires), and an ex-date ON
the valuation date does not (today's close is already ex-dividend). Getting
this one day wrong moves the call-put gap by several vol points.
SXO is the exception to check: index options commonly settle on the OPENING
level of expiry day, which shortens T by most of a day. Confirm against the
contract specification before pricing SXO.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Sequence

import numpy as np

# --------------------------------------------------------------------------
# dividends
# --------------------------------------------------------------------------

Dividend = tuple[float, float]  # (time in years from valuation, cash amount)


def pv_dividends(divs: Sequence[Dividend], r: float, t: float, T: float) -> float:
    """PV at time `t` of dividends with t < t_k <= T, discounted at r."""
    return sum(d * math.exp(-r * (tk - t)) for tk, d in divs if t < tk <= T)


# --------------------------------------------------------------------------
# European (SXO and as a reference leg)
# --------------------------------------------------------------------------

def _norm_cdf(x: float) -> float:
    return 0.5 * math.erfc(-x / math.sqrt(2.0))


def bs_european(S: float, K: float, T: float, r: float, q: float,
                sigma: float, is_call: bool) -> float:
    """Black-Scholes on a continuous yield q. Used for SXO and for reference."""
    if T <= 0:
        intrinsic = (S - K) if is_call else (K - S)
        return max(intrinsic, 0.0)
    if sigma <= 0:
        fwd = S * math.exp((r - q) * T)
        intrinsic = (fwd - K) if is_call else (K - fwd)
        return math.exp(-r * T) * max(intrinsic, 0.0)

    vs = sigma * math.sqrt(T)
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / vs
    d2 = d1 - vs
    if is_call:
        return S * math.exp(-q * T) * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2)
    return K * math.exp(-r * T) * _norm_cdf(-d2) - S * math.exp(-q * T) * _norm_cdf(-d1)


# --------------------------------------------------------------------------
# American, CRR with discrete cash dividends
# --------------------------------------------------------------------------

def min_representable_vol(r: float, b: float, T: float, n: int,
                          safety: float = 2.0) -> float:
    """
    Lowest volatility a CRR tree with `n` steps can represent.

    The branch condition d < e^{(r-b)dt} < u requires sigma > |r-b|*sqrt(dt).
    Below it the risk-neutral probability leaves (0,1) and the lattice is
    meaningless. This matters in practice: it is the floor of your inversion
    bracket, and on short-dated contracts with a fine grid it sits well above
    a naive 1e-4. Quotes that would imply a vol under this floor are almost
    always stale marks or bad prints, not low-vol options.
    """
    return safety * abs(r - b) * math.sqrt(T / n)


def crr_american(S: float, K: float, T: float, r: float, b: float,
                 sigma: float, divs: Sequence[Dividend], is_call: bool,
                 n: int = 400) -> float:
    """
    Cox-Ross-Rubinstein binomial, American exercise, discrete cash dividends.

    r : discount rate (CORRA-based, from the curve object)
    b : continuous borrow / lending fee. Drift on the risky part is (r - b).
        A hard-to-borrow name has b > 0 and it moves the put/call balance.
    divs : [(t, cash), ...] with t in years. Only 0 < t <= T are used.

    Returns the option value. Discretisation error is O(1/n); n=400 is
    ~1e-3 on a 100-strike. Use the SAME n for pricing and inversion in
    production so the error cancels; vary n only to measure convergence.
    """
    if T <= 0:
        intrinsic = (S - K) if is_call else (K - S)
        return max(intrinsic, 0.0)
    if sigma <= 0:
        sigma = 1e-8

    divs = [(t, d) for (t, d) in divs if 0.0 < t <= T]
    pv0 = pv_dividends(divs, r, 0.0, T)
    S_star = S - pv0
    if S_star <= 0.0:
        raise ValueError("PV(dividends) exceeds spot; check the dividend schedule")

    dt = T / n
    u = math.exp(sigma * math.sqrt(dt))
    d = 1.0 / u
    disc = math.exp(-r * dt)
    p = (math.exp((r - b) * dt) - d) / (u - d)
    if not (0.0 < p < 1.0):
        # CRR breaks down when the drift over a step outruns the lattice
        # spacing: the branch condition is d < e^{(r-b)dt} < u, i.e.
        # sigma > |r - b| * sqrt(dt). Below that the tree cannot represent
        # the process at all. Fail loudly rather than clamp p and return a
        # number that looks fine.
        raise ValueError(
            f"risk-neutral probability {p:.4f} outside (0,1): sigma={sigma:.2e} "
            f"is below the lattice floor {min_representable_vol(r, b, T, n):.2e}"
        )

    # remaining-dividend PV at each time step, precomputed
    if divs:
        rem_pv = np.array([pv_dividends(divs, r, i * dt, T) for i in range(n + 1)])
    else:
        rem_pv = np.zeros(n + 1)

    # Node j at step i is S* u^(i-2j). Compute the grid once from the exponent
    # (no accumulated rounding) and slice it: step i uses exponents
    # i, i-2, ..., -i, which sit at grid[n-i : n+i+1 : 2].
    grid = S_star * u ** np.arange(n, -n - 1, -1.0)
    sign = 1.0 if is_call else -1.0
    disc_p, disc_q = disc * p, disc * (1.0 - p)

    V = np.maximum(sign * (grid[::2] + rem_pv[n] - K), 0.0)
    for i in range(n - 1, -1, -1):
        V = disc_p * V[:-1] + disc_q * V[1:]
        ex = sign * (grid[n - i:n + i + 1:2] + (rem_pv[i] - K))
        np.maximum(V, ex, out=V)

    return float(V[0])


# --------------------------------------------------------------------------
# quote hygiene
# --------------------------------------------------------------------------

def mx_tick(price: float, penny_program: bool = False,
            penny_all_levels: bool = False) -> float:
    """
    Minimum price increment for an MX equity or ETF option premium.

    Outside the penny program: 1 cent below $0.50, 5 cents at $0.50 or more
    (in force since February 2022; before that the cutoff was $0.10).
    In the penny program: 1 cent below $3.00, 5 cents at $3.00 or more.
    Some penny-program ETF options tick in 1 cent at every level; pass
    `penny_all_levels=True` for those.

    Which classes are in the program changes, so the caller supplies the flag.
    Confirm the rule against the current MX rulebook before relying on it.
    """
    if penny_all_levels:
        return 0.01
    cutoff = 3.00 if penny_program else 0.50
    return 0.01 if price < cutoff else 0.05


class Reject:
    OK = "ok"
    NON_POSITIVE = "non_positive_price"
    BELOW_INTRINSIC = "below_intrinsic"
    ABOVE_BOUND = "above_upper_bound"
    NO_BRACKET_LOW = "no_bracket_vol_below_floor"
    NO_BRACKET_HIGH = "no_bracket_vol_above_cap"
    NO_ACTIVITY = "no_volume_or_open_interest"
    CROSSED = "crossed_or_locked_market"
    STALE_WIDE = "spread_wider_than_limit"
    ILL_CONDITIONED = "vega_too_small_to_invert"


def screen_quote(price: float, S: float, K: float, T: float, r: float,
                 is_call: bool, volume: int | None = None,
                 open_interest: int | None = None,
                 bid: float | None = None, ask: float | None = None,
                 max_rel_spread: float = 0.75) -> str:
    """
    Pre-inversion filter. Returns Reject.OK or a reason string.

    The activity screen is deliberately OR (volume or OI), and it is the
    caller's job to record how many quotes each reason removed. An error
    surface without a rejection tally is not a measurement.
    """
    if volume is not None and open_interest is not None:
        if volume <= 0 and open_interest <= 0:
            return Reject.NO_ACTIVITY
    if bid is not None and ask is not None:
        if ask < bid:
            return Reject.CROSSED
        mid = 0.5 * (bid + ask)
        if mid > 0 and (ask - bid) / mid > max_rel_spread:
            return Reject.STALE_WIDE
    if price <= 0.0:
        return Reject.NON_POSITIVE

    # American no-arbitrage bounds (immediate exercise is available)
    intrinsic = max(S - K, 0.0) if is_call else max(K - S, 0.0)
    if price < intrinsic - 1e-10:
        return Reject.BELOW_INTRINSIC
    upper = S if is_call else K
    if price > upper + 1e-10:
        return Reject.ABOVE_BOUND
    return Reject.OK


# --------------------------------------------------------------------------
# implied volatility
# --------------------------------------------------------------------------

@dataclass
class IVResult:
    iv: float | None
    reason: str
    iterations: int = 0


def implied_vol_american(price: float, S: float, K: float, T: float, r: float,
                         b: float, divs: Sequence[Dividend], is_call: bool,
                         n: int = 400, lo: float = 1e-4, hi: float = 5.0,
                         tol: float = 1e-6, max_iter: int = 200,
                         min_move_per_vol_pt: float = 0.01) -> IVResult:
    """
    Invert an American option price to implied vol by bisection.

    Bisection, not Newton: vega collapses in the wings and Newton walks off.
    Price is monotone in sigma, so a sign change on [lo, hi] guarantees a root.
    No bracket => an explicit reason, never a fabricated number.

    THE ILL-CONDITIONING CHECK IS NOT OPTIONAL. A deep in-the-money American
    option sitting on its exercise boundary is worth intrinsic for every
    sigma below some threshold. The price carries no volatility information
    at all, and a root finder will happily return the edge of that flat
    region as though it meant something — off by tens of vol points, with no
    error raised. Those quotes must be rejected, not inverted.

    `min_move_per_vol_pt` is the tick-size argument: if one volatility point
    moves the model price by less than a cent, no market can resolve the vol
    and the answer is noise. Default 0.01 = one cent, the smallest MX tick.
    This is a FLOOR that catches the flat exercise region, not a statement of
    how well the vol is known: many MX series tick in 5 cents (see mx_tick),
    and the usable resolution is the quote's half-spread in vol points, which
    the error surface reports. Do not raise this default to the 5-cent tick:
    it would reject at-the-money options on low-priced ETFs.

    `tol` is the bisection width in vol units. 1e-6 is 0.0001 vol points, far
    inside the 0.04-point lattice floor; tighter only costs tree evaluations.
    """
    def f(sig: float) -> float:
        return crr_american(S, K, T, r, b, sig, divs, is_call, n) - price

    # never bracket below what the lattice can represent
    lo = max(lo, min_representable_vol(r, b, T, n))
    if lo >= hi:
        return IVResult(None, Reject.NO_BRACKET_LOW)

    f_lo = f(lo)
    if f_lo > 0.0:
        return IVResult(None, Reject.NO_BRACKET_LOW)
    f_hi = f(hi)
    if f_hi < 0.0:
        return IVResult(None, Reject.NO_BRACKET_HIGH)

    a, b_, fa = lo, hi, f_lo
    it = 0
    while it < max_iter and (b_ - a) > tol:
        m = 0.5 * (a + b_)
        fm = f(m)
        if fa * fm <= 0.0:
            b_ = m
        else:
            a, fa = m, fm
        it += 1

    iv = 0.5 * (a + b_)

    # local vega: is this solve actually identified?
    h = 1e-3
    sig_up, sig_dn = min(iv + h, hi), max(iv - h, lo)
    up = crr_american(S, K, T, r, b, sig_up, divs, is_call, n)
    dn = crr_american(S, K, T, r, b, sig_dn, divs, is_call, n)
    # divide by the bump actually taken: at the edge of the bracket one side
    # is clamped, and dividing by 2h there would halve the vega
    vega = (up - dn) / (sig_up - sig_dn)  # price change per unit vol
    if abs(vega) * 0.01 < min_move_per_vol_pt:
        return IVResult(None, Reject.ILL_CONDITIONED, it)

    return IVResult(iv, Reject.OK, it)


# --------------------------------------------------------------------------
# financing from the chain
# --------------------------------------------------------------------------

def implied_forward_european(call: float, put: float, K: float, T: float,
                             r: float) -> float:
    """
    SXO leg. Parity is an EQUALITY: C - P = e^{-rT}(F - K)  =>  F = K + (C-P)e^{rT}.

    Prefer taking F from the SXF future where it exists and using this only to
    split the discount from the implied index yield: the future is the liquid
    instrument and should carry the weight.
    """
    return K + (call - put) * math.exp(r * T)


def implied_yield_from_forward(F: float, S: float, T: float, r: float) -> float:
    """F = S e^{(r-q)T}  =>  q = r - ln(F/S)/T."""
    return r - math.log(F / S) / T


@dataclass
class FinanceBand:
    lo: float
    hi: float
    width: float
    basis: str


def finance_band_american(call: float, put: float, S: float, K: float,
                          T: float, r: float, div_before_expiry: bool,
                          b_max: float = 0.15,
                          assume_call_european: bool = False) -> FinanceBand:
    """
    Single-name conversion/reversal bound on the implied forward.

        C_am - P_am = e^{-rT}(F - K) + EEP_call - EEP_put,   EEP >= 0

    THE TRAP, and it is worth understanding before you quote a number:

    The tempting move is to pick an expiry with no dividend inside the window,
    argue EEP_call = 0 because early exercise of a call is never optimal
    without a dividend, and collapse the band to one leg. That argument is
    WRONG on exactly the names you are hunting. Borrow cost enters the drift
    as (r - b), so it behaves like a continuous dividend yield: on a
    hard-to-borrow name the American call carries real early-exercise
    premium. The tight bound holds when borrow is small and breaks when
    borrow is large — which is to say it fails precisely when it would have
    told you something.

    So the default here is the honest two-sided band, with the call leg
    bounded by a prior ceiling on borrow:

        EEP_call <= S(1 - e^{-b_max*T}),   EEP_put <= K(1 - e^{-rT})

    `assume_call_european=True` gives the tight one-sided version. Use it
    only where you have independent reason to believe the name is general
    collateral, and never as the default.

    Cross-check every band against the SXO/SXF index-implied rate: a single
    name's implied financing should sit at or above the index. Below it means
    a broken extraction, not a discovered special.
    """
    base = (call - put) * math.exp(r * T) + K
    eep_put_max = K * (math.exp(r * T) - 1.0)

    if assume_call_european and not div_before_expiry:
        return FinanceBand(base, base + eep_put_max, eep_put_max,
                           "one_sided_ASSUMES_general_collateral")

    eep_call_max = S * (1.0 - math.exp(-b_max * T)) * math.exp(r * T)
    if div_before_expiry:
        # the dividend adds further call-side early-exercise value; the caller
        # should supply the schedule if a tighter ceiling is wanted
        eep_call_max += S * (math.exp(r * T) - 1.0)
        basis = "two_sided_div_in_window"
    else:
        basis = "two_sided_borrow_bounded"

    lo, hi = base - eep_call_max, base + eep_put_max
    return FinanceBand(lo, hi, hi - lo, basis)
