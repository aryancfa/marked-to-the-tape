"""
test_voleng.py: the round-trip proof for the pricing and inversion core.

When a real Montreal chain shows a strange number, these tests are how you know
it is the market and not your code.

Two round trips, and the difference matters:
  * engine-consistent: price and invert at the same n. Proves the ROOT FINDER.
    Discretisation error cancels, so this is a weak test on its own.
  * cross-resolution: price at n=1600, invert at production n=400. Proves the
    TREE CONVERGES, and gives the tolerance you can honestly claim.

Run with:  pytest -v
"""

import math

import numpy as np
import pytest

from voleng import (Reject, bs_european, crr_american, finance_band_american,
                    implied_forward_european, implied_vol_american,
                    implied_yield_from_forward, screen_quote)

S, r = 100.0, 0.0375

# Synthetic smile used by the round-trip tests. Never real exchange data.
STRIKES = [80, 90, 95, 100, 105, 110, 125]
EXPIRIES = [0.08, 0.25, 0.75]
B_TRUE, DIVS = 0.0075, [(0.30, 1.45)]


def true_vol(K, T_):
    m = math.log(K / S)
    return 0.20 - 0.55 * m + 0.9 * m * m + 0.03 * math.sqrt(T_)


def borrow_range(band, T_b):
    """Convert a forward band into a borrow-rate band."""
    return (r - math.log(band.hi / S) / T_b, r - math.log(band.lo / S) / T_b)


# ---------------------------------------------------------------------------
# 1. Known identities
# ---------------------------------------------------------------------------

def test_american_call_equals_european_without_dividend_or_borrow():
    am = crr_american(S, 100.0, 0.5, r, 0.0, 0.22, [], is_call=True, n=1200)
    eu = bs_european(S, 100.0, 0.5, r, 0.0, 0.22, is_call=True)
    assert abs(am - eu) < 5e-3


def test_american_put_worth_more_than_european():
    am = crr_american(S, 100.0, 0.5, r, 0.0, 0.22, [], is_call=False, n=1200)
    eu = bs_european(S, 100.0, 0.5, r, 0.0, 0.22, is_call=False)
    assert am > eu + 1e-4


def test_deep_itm_american_put_at_least_intrinsic():
    deep = crr_american(S, 160.0, 0.5, r, 0.0, 0.22, [], is_call=False, n=1200)
    assert deep >= 60.0 - 1e-6


def test_discrete_dividend_creates_call_early_exercise_premium():
    divs = [(0.25, 1.45)]
    am = crr_american(S, 95.0, 0.5, r, 0.0, 0.22, divs, is_call=True, n=1200)
    eu = bs_european(S - 1.45 * math.exp(-r * 0.25), 95.0, 0.5, r, 0.0, 0.22, True)
    assert am > eu + 1e-4


def test_hard_borrow_creates_call_early_exercise_premium_without_dividend():
    """Borrow enters the drift like a yield, so it creates call early-exercise
    value on its own. This is what breaks the tight parity bound in section 4."""
    am = crr_american(S, 100.0, 0.5, r, 0.09, 0.22, [], is_call=True, n=1200)
    eu = bs_european(S, 100.0, 0.5, r, 0.09, 0.22, is_call=True)
    assert am > eu + 1e-4


# ---------------------------------------------------------------------------
# 2. Engine-consistent round trip (proves the root finder)
# ---------------------------------------------------------------------------

def test_recovers_sigma_on_every_invertible_quote():
    """Rejected quotes are deep ITM Americans pinned on the exercise boundary.
    Without the vega screen the solver would return the edge of the flat
    region as if it were a vol, wrong by tens of points, silently."""
    worst, inverted = 0.0, 0
    for T_ in EXPIRIES:
        for K in STRIKES:
            sig = true_vol(K, T_)
            for is_call in (True, False):
                px = crr_american(S, K, T_, r, B_TRUE, sig, DIVS, is_call, n=400)
                res = implied_vol_american(px, S, K, T_, r, B_TRUE, DIVS, is_call, n=400)
                if res.iv is None:
                    continue
                inverted += 1
                worst = max(worst, abs(res.iv - sig))
    assert inverted > 30
    assert worst < 1e-6


# ---------------------------------------------------------------------------
# 3. Cross-resolution round trip (the tolerance you can claim)
# ---------------------------------------------------------------------------

def test_production_tree_within_quarter_vol_point_of_fine_tree():
    """The max error here is the noise floor on the real error surface.
    Anything smaller is your own discretisation, not a model-vs-market signal."""
    errs = []
    for T_ in EXPIRIES:
        for K in STRIKES:
            sig = true_vol(K, T_)
            for is_call in (True, False):
                px = crr_american(S, K, T_, r, B_TRUE, sig, DIVS, is_call, n=1600)
                res = implied_vol_american(px, S, K, T_, r, B_TRUE, DIVS, is_call, n=400)
                if res.iv is not None:
                    errs.append(abs(res.iv - sig))
    errs = np.array(errs)
    assert len(errs) > 30
    assert errs.max() < 0.0025


# ---------------------------------------------------------------------------
# 4. Financing extraction (kept as a documented finding; the feature is cut)
# ---------------------------------------------------------------------------

def test_sxo_parity_recovers_forward_and_yield():
    T_, q_true = 0.5, 0.0125
    c = bs_european(S, 100.0, T_, r, q_true, 0.19, True)
    p = bs_european(S, 100.0, T_, r, q_true, 0.19, False)
    F_hat = implied_forward_european(c, p, 100.0, T_, r)
    F_true = S * math.exp((r - q_true) * T_)
    assert abs(F_hat - F_true) < 1e-9
    assert abs(implied_yield_from_forward(F_hat, S, T_, r) - q_true) < 1e-9


def test_one_sided_band_fails_on_hard_borrow():
    """The finding, not a bug: assuming the call leg is European gives a tight
    band that misses the truth on exactly the hard-to-borrow names it was meant
    to find. Do not ship the one-sided version."""
    def contains(b_t):
        ca = crr_american(S, 100.0, 0.25, r, b_t, 0.24, [], True, n=1200)
        pa = crr_american(S, 100.0, 0.25, r, b_t, 0.24, [], False, n=1200)
        band = finance_band_american(ca, pa, S, 100.0, 0.25, r, False,
                                     assume_call_european=True)
        lo_b, hi_b = borrow_range(band, 0.25)
        return lo_b - 1e-4 <= b_t <= hi_b + 1e-4

    assert contains(0.0025)
    assert not contains(0.0800)


@pytest.mark.parametrize("b_t", [0.0025, 0.0200, 0.0800])
@pytest.mark.parametrize("T_b", [0.10, 0.50])
def test_two_sided_band_contains_truth(b_t, T_b):
    """Valid everywhere, and wide. A single-day band is not a borrow print."""
    ca = crr_american(S, 100.0, T_b, r, b_t, 0.24, [], True, n=1200)
    pa = crr_american(S, 100.0, T_b, r, b_t, 0.24, [], False, n=1200)
    band = finance_band_american(ca, pa, S, 100.0, T_b, r, False)
    lo_b, hi_b = borrow_range(band, T_b)
    assert lo_b - 1e-4 <= b_t <= hi_b + 1e-4


# ---------------------------------------------------------------------------
# 5. Quote hygiene
# ---------------------------------------------------------------------------

def test_rejects_below_intrinsic_call():
    assert screen_quote(3.0, 110.0, 100.0, 0.25, r, True) == Reject.BELOW_INTRINSIC


def test_rejects_crossed_market():
    assert screen_quote(2.0, 100.0, 100.0, .25, r, True, bid=2.2, ask=1.9) == Reject.CROSSED


def test_rejects_dead_strike():
    assert (screen_quote(2.0, 100.0, 100.0, .25, r, True, volume=0, open_interest=0)
            == Reject.NO_ACTIVITY)


def test_rejects_blown_out_spread():
    assert (screen_quote(2.0, 100.0, 100.0, .25, r, True, bid=0.05, ask=4.0)
            == Reject.STALE_WIDE)


def test_accepts_live_quote():
    assert screen_quote(4.1, 100.0, 100.0, .25, r, True, volume=42, open_interest=900,
                        bid=4.0, ask=4.2) == Reject.OK


def test_no_bracket_returns_reason_not_fabricated_vol():
    res = implied_vol_american(0.001, S, 60.0, 0.05, r, 0.0, [], is_call=True, n=200)
    assert res.iv is None and res.reason == Reject.NO_BRACKET_LOW


def test_pinned_deep_itm_put_rejected_not_inverted():
    res = implied_vol_american(30.0, S, 130.0, 0.08, r, 0.0, [], is_call=False, n=400)
    assert res.iv is None and res.reason == Reject.ILL_CONDITIONED
