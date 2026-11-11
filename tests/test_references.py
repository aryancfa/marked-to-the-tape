"""
test_references.py: the three references behind the front chart (build
sheet B3), on made-up vols and a SYNTHETIC chain. Nothing here is market data.
"""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from references import (PAIR, SMILE, EXCHANGE, fit_smile, pool_buckets,
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
    assert p["std_error"] == pytest.approx(np.std(daily, ddof=1) / np.sqrt(5))
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
