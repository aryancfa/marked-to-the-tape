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

from voleng import (Reject, bs_european, bs_greeks, crr_american,
                    finance_band_american, greeks_american,
                    implied_spot_american,
                    implied_forward_european, implied_vol_american,
                    implied_yield_from_forward, mx_tick, screen_quote)

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

def test_production_tree_within_noise_floor_of_fine_tree():
    """The max error here is the solver's noise floor: about 0.04 vol points,
    measured at 0.037 on this grid. The bound is 0.05 so the documented claim
    is the thing being tested. Anything smaller is your own discretisation,
    not a model-vs-market signal."""
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
    assert errs.max() < 0.0005


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


# ---------------------------------------------------------------------------
# 6. Time convention: which dividends count
# ---------------------------------------------------------------------------

def test_ex_date_on_expiry_counts():
    """An ex-date on the expiry date has already knocked the share down when
    the option expires, so the put is worth clearly more than with no
    dividend. The call loses much less than the put gains, because its holder
    exercises the day before and collects the dividend."""
    T_ = 30 / 365
    divs = [(T_, 1.0)]
    put_gain = (crr_american(S, 100.0, T_, r, 0.0, 0.2, divs, False)
                - crr_american(S, 100.0, T_, r, 0.0, 0.2, [], False))
    call_loss = (crr_american(S, 100.0, T_, r, 0.0, 0.2, [], True)
                 - crr_american(S, 100.0, T_, r, 0.0, 0.2, divs, True))
    assert put_gain > 0.3
    assert 0.0 < call_loss < 0.5 * put_gain


def test_ex_date_on_valuation_date_or_after_expiry_is_ignored():
    """Today's close is already ex-dividend, and a dividend after expiry never
    touches the option. Both must price exactly like no dividend."""
    T_ = 30 / 365
    without = crr_american(S, 100.0, T_, r, 0.0, 0.2, [], False)
    today = crr_american(S, 100.0, T_, r, 0.0, 0.2, [(0.0, 1.0)], False)
    later = crr_american(S, 100.0, T_, r, 0.0, 0.2, [(T_ + 1 / 365, 1.0)], False)
    assert today == without and later == without


# ---------------------------------------------------------------------------
# 7. Tick size and the vega screen at the edge of the bracket
# ---------------------------------------------------------------------------

def test_mx_tick_rules():
    assert mx_tick(0.49) == 0.01 and mx_tick(0.50) == 0.05
    assert mx_tick(2.99, penny_program=True) == 0.01
    assert mx_tick(3.00, penny_program=True) == 0.05
    assert mx_tick(12.0, penny_all_levels=True) == 0.01


def test_vega_screen_uses_the_bump_actually_taken():
    """With the solve pinned at the top of the bracket the up-bump is clamped.
    Dividing by the full bump would halve the vega and reject a quote that is
    in fact well identified."""
    hi = 0.30
    px = crr_american(S, 100.0, 0.25, r, 0.0, hi, [], True)
    res = implied_vol_american(px, S, 100.0, 0.25, r, 0.0, [], True, hi=hi,
                               min_move_per_vol_pt=0.15)
    assert res.reason == Reject.OK and abs(res.iv - hi) < 1e-5


# ---------------------------------------------------------------------------
# 8. Greeks (build sheet B7: analytic against bumped repricing)
# ---------------------------------------------------------------------------

def test_bs_greeks_match_bumped_repricing():
    """The formulas are checked against the definition: move one input a
    little, reprice, take the difference."""
    args = dict(S=S, K=100.0, T=0.5, r=r, q=0.0125, sigma=0.19, is_call=False)
    g = bs_greeks(**args)

    def f(**k):
        return bs_european(**{**args, **k})

    h = 0.01
    assert g.delta == pytest.approx((f(S=S + h) - f(S=S - h)) / (2 * h), abs=1e-7)
    assert g.gamma == pytest.approx((f(S=S + h) - 2 * f() + f(S=S - h)) / h**2, abs=1e-6)
    assert g.vega == pytest.approx(f(sigma=0.20) - f(sigma=0.19), rel=0.01)
    assert g.theta == pytest.approx(f(T=0.5 - 1 / 365) - f(), rel=0.01)
    assert g.rho == pytest.approx((f(r=r + 1e-5) - f(r=r - 1e-5)) / 2e-5 * 0.01, abs=1e-7)


@pytest.mark.parametrize("K,T_", [(100.0, 0.25), (90.0, 0.25), (110.0, 0.25),
                                 (100.0, 30 / 365), (105.0, 0.75)])
def test_american_call_greeks_match_black_scholes_without_dividend(K, T_):
    """No dividend and no borrow means the American call is never exercised
    early, so its Greeks must equal the European ones. Gamma is the hard one:
    a bumped-share gamma failed this by a factor of three."""
    a = greeks_american(S, K, T_, r, 0.0, 0.22, [], True)
    e = bs_greeks(S, K, T_, r, 0.0, 0.22, True)
    assert a.delta == pytest.approx(e.delta, abs=2e-3)
    assert a.gamma == pytest.approx(e.gamma, rel=0.01)
    assert a.vega == pytest.approx(e.vega, rel=0.02)
    assert a.theta == pytest.approx(e.theta, rel=0.03)
    assert a.rho == pytest.approx(e.rho, rel=0.01)


def test_american_greeks_have_the_right_signs_with_a_dividend():
    divs = [(0.10, 1.2)]
    call = greeks_american(S, 100.0, 0.25, r, 0.0, 0.22, divs, True)
    put = greeks_american(S, 100.0, 0.25, r, 0.0, 0.22, divs, False)
    assert 0.0 < call.delta < 1.0 and -1.0 < put.delta < 0.0
    for g in (call, put):
        assert g.gamma > 0.0 and g.vega > 0.0 and g.theta < 0.0
    assert call.rho > 0.0 > put.rho


# ---------------------------------------------------------------------------
# 9. Share price implied by the option quotes
# ---------------------------------------------------------------------------

def _pairs(S_true, T_, divs, strikes=(97.5, 100.0, 102.5), sig=0.20):
    return [(K, crr_american(S_true, K, T_, r, 0.0, sig, divs, True),
             crr_american(S_true, K, T_, r, 0.0, sig, divs, False))
            for K in strikes]


@pytest.mark.parametrize("divs", [[], [(10 / 365, 1.0)]])
def test_implied_spot_recovers_the_price_the_options_were_struck_against(divs):
    """Prices are made at 100.37 and the inverter is told only 'somewhere
    between 99 and 101'. It must find 100.37 to within a basis point."""
    T_, S_true = 30 / 365, 100.37
    res = implied_spot_american(_pairs(S_true, T_, divs), T_, r, 0.0, divs, 99.0, 101.0)
    assert res.reason == Reject.OK and res.pairs_used == 3
    assert abs(res.spot / S_true - 1.0) < 1e-4
    assert abs(res.gap_at_spot) < 1e-4


def test_implied_spot_narrows_a_bracket_that_is_too_wide_for_a_short_expiry():
    """One week out, a share price 1.5% away puts the options below exercise
    value, so the ends of the bracket have no implied vol. The bracket must
    narrow itself and still find the price."""
    T_, S_true = 7 / 365, 100.37
    pairs = _pairs(S_true, T_, [], strikes=(99.0, 100.0, 101.0), sig=0.15)
    res = implied_spot_american(pairs, T_, r, 0.0, [], S_true * 0.985, S_true * 1.015)
    assert res.reason == Reject.OK
    assert abs(res.spot / S_true - 1.0) < 1e-4
    # with narrowing switched off, the same call refuses
    off = implied_spot_american(pairs, T_, r, 0.0, [], S_true * 0.985,
                                S_true * 1.015, max_shrinks=0)
    assert off.spot is None and off.reason == Reject.TOO_FEW_PAIRS


def test_implied_spot_refuses_when_the_bracket_misses():
    T_ = 30 / 365
    res = implied_spot_american(_pairs(100.37, T_, []), T_, r, 0.0, [], 99.6, 100.0)
    assert res.spot is None and res.reason == Reject.NO_SPOT_BRACKET


def test_implied_spot_refuses_with_too_few_pairs():
    T_ = 30 / 365
    one = _pairs(100.37, T_, [], strikes=(100.0,))
    res = implied_spot_american(one, T_, r, 0.0, [], 99.0, 101.0, max_shrinks=1)
    assert res.spot is None and res.reason == Reject.TOO_FEW_PAIRS


def test_implied_vol_reports_the_vega_it_measured():
    """The solver already measures vega to decide whether the vol is
    identified. Handing it back lets a caller turn a price spread into a vol
    spread without two more inversions."""
    from voleng import greeks_american
    args = (100.0, 100.0, 0.25, 0.03, 0.0)
    px = crr_american(*args, 0.20, [], True)
    res = implied_vol_american(px, *args, [], True)
    g = greeks_american(*args, 0.20, [], True)
    assert res.vega == pytest.approx(g.vega, rel=0.02)      # per vol point
    # a rejected quote carries no vega
    assert implied_vol_american(1e-9, *args, [], True).vega is None


# ---------------------------------------------------------------------------
# The solver refuses inputs it cannot answer for
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad", [dict(price=float("nan")), dict(price=float("inf")),
                                 dict(S=float("nan")), dict(K=0.0), dict(T=-0.1),
                                 dict(r=float("inf"))])
def test_a_price_or_input_that_is_not_a_finite_number_gets_no_vol(bad):
    """nan compares False with everything, so an unchecked nan price walks
    through every bracket test and comes out as a confident 500% vol."""
    args = dict(price=5.0, S=100.0, K=100.0, T=0.5, r=0.03)
    args.update(bad)
    res = implied_vol_american(args["price"], args["S"], args["K"], args["T"], args["r"],
                               0.0, [], True)
    assert res.iv is None and res.reason == Reject.BAD_INPUT


def test_a_non_positive_price_keeps_its_own_reason():
    res = implied_vol_american(0.0, 100.0, 100.0, 0.5, 0.03, 0.0, [], True)
    assert res.iv is None and res.reason == Reject.NON_POSITIVE


def test_running_out_of_iterations_is_reported_and_no_vol_is_returned():
    none = implied_vol_american(5.0, 100.0, 100.0, 0.5, 0.03, 0.0, [], True, max_iter=0)
    few = implied_vol_american(5.0, 100.0, 100.0, 0.5, 0.03, 0.0, [], True, max_iter=5)
    full = implied_vol_american(5.0, 100.0, 100.0, 0.5, 0.03, 0.0, [], True)
    assert none.iv is None and none.reason == Reject.NOT_CONVERGED
    assert few.iv is None and few.reason == Reject.NOT_CONVERGED and few.iterations == 5
    assert full.reason == Reject.OK and full.iv == pytest.approx(0.1569, abs=0.01)
