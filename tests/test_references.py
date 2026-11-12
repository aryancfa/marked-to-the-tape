"""
test_references.py: the three references behind the front chart (build
sheet B3), on made-up vols and a SYNTHETIC chain. Nothing here is market data.
"""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from surface import mean_std_error
from references import (PAIR, SMILE, EXCHANGE, fit_smile, fit_svi, forward_price,
                        pool_buckets, svi_butterfly_free, svi_total_variance,
                        atm_total_variance, calendar_violations, fit_level, fit_ssvi,
                        smile_counts, ssvi_butterfly_free, ssvi_total_variance,
                        reference_differences, sensitivity_table, tightest)
from surface import dividend_times, invert_chain
from tradingcal import year_fraction
from voleng import crr_american

VAL, NEAR = date(2026, 11, 2), date(2026, 12, 18)
S0, R, HALF = 50.0, 0.03, 0.03
STRIKES = (46.0, 47.5, 49.0, 50.0, 51.0, 52.5, 54.0)


# ---------------------------------------------------------------------------
# 1. The smile fit
# ---------------------------------------------------------------------------

def smile(k):
    """Lopsided, like a real equity smile: a cubic term tilts the wings."""
    return 0.20 - 0.30 * k + 0.80 * k * k - 2.0 * k ** 3


WIDE_K = np.log(np.array([42.5, 45.0, 47.5, 50.0, 52.5, 55.0, 57.5]) / S0)


def test_fit_recovers_a_lopsided_smile():
    fit = fit_smile(WIDE_K, smile(WIDE_K), np.ones_like(WIDE_K))
    assert fit.coef == pytest.approx((0.20, -0.30, 0.80, -2.0), abs=1e-7)
    assert fit.rms < 1e-9
    assert fit.residual == pytest.approx(np.zeros(7), abs=1e-9)


def test_a_quadratic_leaves_a_mirror_image_pattern_on_a_lopsided_smile():
    """Why the curve has a cubic term. Fit a plain quadratic to a lopsided
    smile and the misses flip sign from one side of the money to the other,
    the same way every day. Pooled across days that looks like a real
    disagreement, and it is only the curve being the wrong shape."""
    q = fit_smile(WIDE_K, smile(WIDE_K), np.ones_like(WIDE_K), degree=2)
    r = q.residual
    assert np.abs(r).max() > 0.002                       # misses by over 0.2 vol points
    assert r[0] * r[-1] < 0                              # opposite signs in the two wings


def test_each_strike_is_judged_by_a_curve_fitted_without_it():
    """One strike sits 2 vol points above a clean smile. A curve fitted
    through all seven would bend toward it and report less than 2. The curve
    fitted through the other six reports exactly 2."""
    iv = smile(WIDE_K)
    iv[3] += 0.02
    fit = fit_smile(WIDE_K, iv, np.ones_like(WIDE_K))
    assert fit.residual[3] == pytest.approx(0.02, abs=1e-9)
    k = WIDE_K[3]
    ordinary = iv[3] - sum(c * k ** i for i, c in enumerate(fit.coef))
    assert 0 < ordinary < 0.02


def test_weights_decide_which_points_the_curve_follows():
    """Same data, but the odd point has almost no vega. The curve ignores
    it, so the others show no residual."""
    iv = smile(WIDE_K)
    iv[6] += 0.05
    w = np.array([1, 1, 1, 1, 1, 1, 1e-9])
    fit = fit_smile(WIDE_K, iv, w)
    assert np.abs(fit.residual[:6]).max() < 1e-6


def test_too_few_strikes_give_no_fit():
    """A cubic passes through any four strikes. With no strike to spare
    there is no reference, and none is made up."""
    k = WIDE_K[:4]
    assert fit_smile(k, smile(k), np.ones(4)) is None
    assert fit_smile(WIDE_K[:5], smile(WIDE_K[:5]), np.ones(5)) is not None


# ---------------------------------------------------------------------------
# 2. Differences against the three references, on a synthetic chain
# ---------------------------------------------------------------------------

def chain(vol_of_strike):
    T = year_fraction(VAL, NEAR)
    rows = []
    for K in STRIKES:
        for cp in "CP":
            sig = vol_of_strike(K)
            px = crr_american(S0, K, T, R, 0.0, sig, [], cp == "C")
            rows.append(dict(
                date=pd.Timestamp(VAL), underlying="ZZZ", expiry=pd.Timestamp(NEAR),
                strike=K, call_put=cp, bid=px - HALF, ask=px + HALF, settle=px,
                volume=0, open_interest=0, traded=cp == "C", two_sided=True,
                tier="traded" if cp == "C" else "quoted", mark=px, mark_source="mid",
                mx_iv=sig + 0.01))
    return pd.DataFrame(rows)


@pytest.fixture(scope="module")
def diffs():
    inv = invert_chain(chain(lambda K: smile(np.log(K / S0))), S0, R, [])
    assert (inv["reason"] == "ok").all()
    assert (inv["vega"] > 0).all()                 # the solver's vega is kept
    return reference_differences(inv, S0)


def test_a_clean_chain_shows_no_difference_on_the_first_two_references(diffs):
    for ref in (PAIR, SMILE):
        d = diffs[diffs["reference"] == ref]
        assert len(d) and d["diff"].abs().max() < 0.02          # vol points


def test_exchange_difference_is_ours_minus_theirs_by_side(diffs):
    d = diffs[diffs["reference"] == EXCHANGE]
    assert set(d["side"]) == {"C", "P"} and len(d) == 14
    assert d["diff"].values == pytest.approx(-1.0, abs=0.01)    # they are 1 point above


def test_every_difference_carries_its_bucket_inputs_and_quote_bar(diffs):
    assert {"date", "expiry", "days", "moneyness", "reference", "side", "diff",
            "quote_bar", "tier"} <= set(diffs.columns)
    pair = diffs[diffs["reference"] == PAIR]
    assert len(pair) == 7 and (pair["side"] == "pair").all()
    single = diffs[(diffs["reference"] == SMILE) & (diffs["strike"] == 50.0)]
    # a pair's bar is the two quotes' bars added
    assert pair[pair["strike"] == 50.0]["quote_bar"].iloc[0] == pytest.approx(
        single["quote_bar"].sum(), rel=1e-6)
    # a pair is only as good as its weaker side
    assert (pair["tier"] == "quoted").all()


def test_one_bad_quote_stands_out_against_the_smile():
    """Both quotes at one strike sit 2 vol points high. Judged by a curve
    fitted through the other strikes, that strike shows the full 2 points.
    (Its neighbours show something too, because the bad strike is in THEIR
    curve. That is why a single day is never read alone.)"""
    def bumped(K):
        return smile(np.log(K / S0)) + (0.02 if K == 52.5 else 0.0)
    d = reference_differences(invert_chain(chain(bumped), S0, R, []), S0)
    s = d[d["reference"] == SMILE]
    odd = s[s["strike"] == 52.5]["diff"]
    assert len(odd) == 2 and odd.values == pytest.approx(2.0, abs=0.05)


# ---------------------------------------------------------------------------
# 3. Pooling across days
# ---------------------------------------------------------------------------

def frame(day_values, moneyness=1.0, days=40, bar=1.0, reference=PAIR, side="pair"):
    return pd.DataFrame([dict(date=pd.Timestamp(2026, 11, d), expiry=NEAR, days=days,
                              strike=50.0, moneyness=moneyness, reference=reference,
                              side=side, diff=v, quote_bar=bar, tier="quoted")
                         for d, vals in day_values.items() for v in vals])


def test_pooling_averages_within_a_day_first_so_a_day_counts_once():
    """Day 2 has two points (0.2, 0.4), days 3 to 6 one each."""
    d = frame({2: [0.2, 0.4], 3: [0.3], 4: [0.3], 5: [0.1], 6: [0.5]})
    p = pool_buckets(d).iloc[0]
    daily = [0.3, 0.3, 0.3, 0.1, 0.5]
    assert p["days"] == 5 and p["points"] == 6
    assert p["mean"] == pytest.approx(np.mean(daily))
    assert p["std_error_iid"] == pytest.approx(np.std(daily, ddof=1) / np.sqrt(5))
    assert p["std_error"] == pytest.approx(mean_std_error(daily))
    assert p["t_stat"] == pytest.approx(p["mean"] / p["std_error"])


def test_a_disagreement_needs_two_standard_errors_and_enough_days():
    steady = frame({d: [0.30 + 0.01 * (d % 2)] for d in range(2, 9)})
    noisy = frame({d: [0.30 * (-1) ** d] for d in range(2, 9)})
    few = frame({2: [0.30], 3: [0.31], 4: [0.30]})
    assert bool(pool_buckets(steady).iloc[0]["disagreement"])
    assert not bool(pool_buckets(noisy).iloc[0]["disagreement"])
    assert not bool(pool_buckets(few).iloc[0]["disagreement"])   # three days prove little


def test_buckets_split_by_moneyness_and_days():
    d = pd.concat([frame({2: [0.1], 3: [0.1]}, moneyness=0.96, days=20),
                   frame({2: [0.5], 3: [0.5]}, moneyness=1.03, days=150)])
    p = pool_buckets(d).set_index(["days_bucket", "moneyness_bucket"])["mean"]
    assert len(p) == 2 and sorted(p.round(2)) == [0.1, 0.5]


def test_tightest_keeps_the_third_with_the_narrowest_quotes():
    d = pd.concat([frame({2: [0.0]}, bar=b) for b in (0.5, 0.6, 0.7, 2.0, 2.5, 3.0)])
    t = tightest(d)
    assert sorted(t["quote_bar"]) == [0.5, 0.6]


# ---------------------------------------------------------------------------
# 4. How far a stated input error moves the call-put gap
# ---------------------------------------------------------------------------

def test_sensitivity_table_has_the_right_signs_and_shape():
    """A share price read too low, a rate too low or a dividend too high all
    make the model's forward too low, so calls look dear: a positive gap.
    A spot error fades with maturity; a rate error grows with it."""
    t = sensitivity_table(S=100.0, sigma=0.18, r=0.03, dividend=0.9,
                          days=(30, 90, 180))
    assert list(t.index) == [30, 90, 180]
    assert (t > 0).all().all()
    assert t["spot 10 bp low"].is_monotonic_decreasing
    assert t["rate 25 bp low"].is_monotonic_increasing
    assert t.loc[30, "spot 10 bp low"] == pytest.approx(0.9, abs=0.3)


def test_the_published_vol_can_come_from_another_column():
    """The same comparison serves a second model: OptionMetrics' vol sits in
    its own column, and the reference is named for what it is."""
    inv = invert_chain(chain(lambda K: smile(np.log(K / S0))), S0, R, [])
    inv = inv.drop(columns="mx_iv").assign(om_iv=inv["iv"] - 0.005)
    d = reference_differences(inv, S0, published="om_iv")
    x = d[d["reference"] == EXCHANGE]
    assert len(x) == 14 and x["diff"].values == pytest.approx(0.5, abs=1e-9)
    assert reference_differences(inv, S0)[lambda t: t["reference"] == EXCHANGE].empty


# ---------------------------------------------------------------------------
# 5. The SVI smile, against the forward
# ---------------------------------------------------------------------------

SVI_TRUE = (0.004, 0.08, -0.45, 0.02, 0.10)        # a, b, rho, m, s: made up
SVI_T = 0.25
SVI_K = np.linspace(-0.12, 0.12, 11)


def svi_vol(k):
    return np.sqrt(svi_total_variance(SVI_TRUE, k) / SVI_T)


def test_svi_recovers_a_smile_of_its_own_shape():
    fit = fit_svi(SVI_K, svi_vol(SVI_K), np.ones_like(SVI_K), SVI_T)
    assert fit is not None
    again = np.sqrt(svi_total_variance(fit.coef, SVI_K) / SVI_T)
    assert again == pytest.approx(svi_vol(SVI_K), abs=2e-4)        # 0.02 vol points
    assert np.abs(fit.residual).max() < 5e-4


def test_svi_judges_each_strike_by_a_curve_fitted_without_it():
    """One strike sits 2 vol points above a clean smile; the curve through
    the others reports close to 2, not something bent toward the bad quote."""
    iv = svi_vol(SVI_K)
    iv[5] += 0.02
    fit = fit_svi(SVI_K, iv, np.ones_like(SVI_K), SVI_T)
    assert fit.residual[5] == pytest.approx(0.02, abs=1e-3)
    assert np.abs(np.delete(fit.residual, 5)).max() < 0.012


def test_the_fitted_curve_admits_no_butterfly_arbitrage():
    fit = fit_svi(SVI_K, svi_vol(SVI_K), np.ones_like(SVI_K), SVI_T)
    assert svi_butterfly_free(fit.coef, -1.0, 1.0)


def test_a_curve_with_a_butterfly_arbitrage_is_recognised():
    """A published counter-example (Axel Vogt's): five harmless-looking
    numbers that imply a negative probability near the money."""
    bad = (-0.0410, 0.1331, 0.3060, 0.3586, 0.4153)
    assert not svi_butterfly_free(bad, -1.5, 1.5)


def test_svi_needs_seven_strikes():
    k = SVI_K[:6]
    assert fit_svi(k, svi_vol(k), np.ones(6), SVI_T) is None
    assert fit_svi(SVI_K[:7], svi_vol(SVI_K[:7]), np.ones(7), SVI_T) is not None


def test_forward_is_the_share_price_less_dividends_grown_at_the_rate():
    assert forward_price(100.0, 0.5, 0.0, []) == pytest.approx(100.0)
    assert forward_price(100.0, 1.0, 0.03, []) == pytest.approx(100.0 * np.exp(0.03))
    f = forward_price(100.0, 0.5, 0.03, [(0.25, 1.0), (0.75, 1.0)])   # second is after expiry
    assert f == pytest.approx((100.0 - np.exp(-0.03 * 0.25)) * np.exp(0.015))


def test_smile_differences_use_svi_by_default_and_the_cubic_on_request(diffs):
    assert (diffs["reference"] == SMILE).sum() == 2 * len(STRIKES)
    inv = invert_chain(chain(lambda K: smile(np.log(K / S0))), S0, R, [])
    cubic = reference_differences(inv, S0, smile="cubic")
    assert (cubic["reference"] == SMILE).sum() == 2 * len(STRIKES)


# ---------------------------------------------------------------------------
# 6. One surface across expiries, and the check across time
# ---------------------------------------------------------------------------

SS = dict(rho=-0.4, eta=0.9, gamma=0.4)
SS_T = (0.1, 0.25, 0.5, 1.0)
SS_THETA = tuple(0.04 * t for t in SS_T)             # a flat 20% at the money


def ssvi_slice(T, theta, k):
    return k, np.sqrt(ssvi_total_variance(theta, k, **SS) / T), np.ones_like(k), T


def test_at_the_money_variance_is_read_between_the_strikes_around_the_forward():
    k = np.array([-0.05, 0.05, -0.05, 0.05])
    iv = np.array([0.22, 0.18, 0.22, 0.18])
    assert atm_total_variance(k, iv, np.ones(4), 0.5) == pytest.approx(
        0.5 * (0.22 ** 2 + 0.18 ** 2) / 2)
    assert np.isnan(atm_total_variance(np.array([0.02, 0.05]), iv[:2], np.ones(2), 0.5))


def test_surface_recovers_its_own_shape_across_expiries():
    k = np.linspace(-0.1, 0.1, 9)
    fit = fit_ssvi([ssvi_slice(T, th, k) for T, th in zip(SS_T, SS_THETA)])
    assert fit is not None and fit.raised == 0
    assert fit.rms < 2e-3                              # within 0.2 vol points
    assert fit.rho == pytest.approx(SS["rho"], abs=0.1)
    assert fit.eta * (1 + abs(fit.rho)) <= 2.0 + 1e-9  # inside the no-arbitrage region
    assert 0.0 < fit.gamma <= 0.5


def test_a_level_that_falls_with_time_is_lifted_and_counted():
    """The third expiry is given LESS at-the-money variance than the second.
    That is a calendar arbitrage; the surface lifts it and says so."""
    k = np.linspace(-0.1, 0.1, 9)
    theta = (0.004, 0.010, 0.008, 0.040)
    fit = fit_ssvi([ssvi_slice(T, th, k) for T, th in zip(SS_T, theta)])
    assert fit.raised == 1
    assert list(fit.theta) == sorted(fit.theta)


def test_curves_that_cross_in_time_are_counted_as_calendar_violations():
    flat = lambda level: (level, 0.0, 0.0, 0.0, 0.1)   # SVI with b = 0: a flat line
    rising = [(0.25, flat(0.010), -0.1, 0.1), (0.5, flat(0.020), -0.1, 0.1)]
    falling = [(0.25, flat(0.020), -0.1, 0.1), (0.5, flat(0.010), -0.1, 0.1)]
    apart = [(0.25, flat(0.020), -0.1, -0.05), (0.5, flat(0.010), 0.05, 0.1)]
    assert calendar_violations(rising) == (1, 0)
    assert calendar_violations(falling) == (1, 1)
    assert calendar_violations(apart) == (0, 0)        # no strikes in common: not tested


def test_every_expiry_is_counted_as_fitted_one_way_or_not_at_all(diffs):
    c = diffs.attrs["smile"]
    assert c["expiries"] == 1 and c["slice"] == 1
    assert c["surface"] == 0 and c["unfitted"] == 0
    assert (diffs.loc[diffs["reference"] == SMILE, "smile_model"] == "slice").all()
    assert smile_counts([diffs, diffs])["slice"] == 2


def test_a_level_is_fitted_and_not_read_off_a_line_across_the_smile():
    """Three strikes far apart on a curved smile. A straight line between the
    two around the forward sits ABOVE the curve at the forward; the fitted
    level lands on the true one."""
    theta, T = 0.02, 0.5
    k = np.array([-0.10, 0.08, 0.12])
    iv = np.sqrt(ssvi_total_variance(theta, k, **SS) / T)
    line = atm_total_variance(k, iv, np.ones(3), T)
    fitted = fit_level(k, iv, np.ones(3), T, **SS)
    assert line > theta * 1.02
    assert fitted == pytest.approx(theta, rel=0.01)
    assert fit_level(k, iv, np.ones(3), T, floor=0.03, **SS) == pytest.approx(0.03)


def test_the_shortcut_is_not_a_guarantee_once_total_variance_is_large():
    """eta * (1 + |rho|) <= 2 was the documented condition. It implies the
    real ones only while at-the-money total variance stays at or below 1. A
    surface with theta of 4 (200% vol for a year) passes the shortcut and
    fails the real test, so the fitter must go by the real test."""
    rho, eta, gamma = 0.0, 2.0, 0.1
    assert eta * (1 + abs(rho)) <= 2.0
    assert ssvi_butterfly_free(0.04, rho, eta, gamma)          # an ordinary equity level
    assert not ssvi_butterfly_free(4.0, rho, eta, gamma)       # the stress case
    assert not ssvi_butterfly_free(0.04, rho, eta, 0.7)        # gamma above one half


def test_the_fitted_surface_passes_the_real_conditions():
    k = np.linspace(-0.1, 0.1, 9)
    fit = fit_ssvi([ssvi_slice(T, th, k) for T, th in zip(SS_T, SS_THETA)])
    assert ssvi_butterfly_free(max(fit.theta), fit.rho, fit.eta, fit.gamma)


# ---------------------------------------------------------------------------
# 9. A second bad quote in the same expiry must not hide the first
# ---------------------------------------------------------------------------

def two_bad_quotes():
    iv = svi_vol(SVI_K)
    iv[3] -= 0.04           # 4 vol points low
    iv[6] += 0.02           # 2 vol points high, three strikes away
    return iv


def test_two_bad_quotes_are_each_reported_at_their_own_size():
    fit = fit_svi(SVI_K, two_bad_quotes(), np.ones_like(SVI_K), SVI_T)
    assert fit is not None
    assert fit.residual[3] == pytest.approx(-0.04, abs=4e-3)
    assert fit.residual[6] == pytest.approx(0.02, abs=4e-3)
    clean = np.delete(fit.residual, [3, 6])
    assert np.abs(clean).max() < 7e-3          # good strikes stay well under either error


def test_plain_least_squares_lets_one_bad_quote_hide_the_other():
    """Why the robust fit exists: this is what the benchmark found."""
    fit = fit_svi(SVI_K, two_bad_quotes(), np.ones_like(SVI_K), SVI_T, robust=False)
    off = (fit is None or not np.isfinite(fit.residual[3])
           or abs(fit.residual[3] + 0.04) > 4e-3 or abs(fit.residual[6] - 0.02) > 4e-3
           or np.abs(np.delete(fit.residual, [3, 6])).max() > 4e-3)
    assert off


def test_the_robust_fit_leaves_a_clean_smile_alone():
    k = SVI_K
    plain = fit_svi(k, svi_vol(k), np.ones_like(k), SVI_T, robust=False)
    robust = fit_svi(k, svi_vol(k), np.ones_like(k), SVI_T)
    assert robust.coef == pytest.approx(plain.coef, abs=1e-9)
    assert robust.residual == pytest.approx(plain.residual, abs=1e-9)


def test_ordinary_scatter_is_not_treated_as_bad_quotes():
    """Noise of 0.1 vol points on every strike: the two fits must agree to
    well inside that noise, or the robust fit is throwing good quotes away."""
    rng = np.random.default_rng(3)
    iv = svi_vol(SVI_K) + rng.normal(0.0, 0.001, len(SVI_K))
    plain = fit_svi(SVI_K, iv, np.ones_like(SVI_K), SVI_T, robust=False)
    robust = fit_svi(SVI_K, iv, np.ones_like(SVI_K), SVI_T)
    assert np.abs(robust.residual - plain.residual).max() < 5e-4
