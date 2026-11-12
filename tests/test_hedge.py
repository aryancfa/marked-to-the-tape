"""
test_hedge.py: hedge ratios from earlier days only (build sheet C4), and the
Compustat price reader, on made-up returns and a made-up file.
"""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from hedge import (autocorrelation, full_sample_ratio, hedge_residual, rolling_ratio,
                   variance_removed)
from loaders.compustat import load_index_returns, load_total_returns

RNG = np.random.default_rng(7)
N = 1500
DAYS = pd.bdate_range("2015-01-01", periods=N)


def world(beta=1.2, sector_beta=0.0, noise=0.006):
    index = pd.Series(RNG.normal(0, 0.010, N), DAYS, name="index")
    sector = pd.Series(RNG.normal(0, 0.008, N), DAYS, name="sector")
    share = beta * index + sector_beta * sector + RNG.normal(0, noise, N)
    return pd.Series(share, DAYS, name="share"), index, sector


# ---------------------------------------------------------------------------
# 1. The ratio on a day comes only from the days before it
# ---------------------------------------------------------------------------

def test_rolling_ratio_recovers_the_true_ratio():
    share, index, _ = world(beta=1.2)
    b = rolling_ratio(share, index.to_frame(), window=250)
    assert b["index"].iloc[:250].isna().all()            # nothing until a full window
    assert b["index"].iloc[250:].mean() == pytest.approx(1.2, abs=0.03)


def test_a_day_never_sees_itself_or_the_future():
    """Put an absurd move on one day. The ratio USED on that day must not
    change. The ratio used the next day must."""
    share, index, _ = world()
    before = rolling_ratio(share, index.to_frame(), window=250)
    t = 600
    shocked = share.copy()
    shocked.iloc[t] += 0.5 * np.sign(index.iloc[t])
    after = rolling_ratio(shocked, index.to_frame(), window=250)
    assert after["index"].iloc[t] == before["index"].iloc[t]
    assert after["index"].iloc[:t].equals(before["index"].iloc[:t])
    assert after["index"].iloc[t + 1] != before["index"].iloc[t + 1]


def test_two_hedges_are_fitted_together():
    share, index, sector = world(beta=1.0, sector_beta=0.7)
    X = pd.concat([index, sector], axis=1)
    b = rolling_ratio(share, X, window=500).dropna()
    assert b["index"].mean() == pytest.approx(1.0, abs=0.04)
    assert b["sector"].mean() == pytest.approx(0.7, abs=0.05)


# ---------------------------------------------------------------------------
# 2. What the hedge removes
# ---------------------------------------------------------------------------

def test_variance_removed_matches_what_the_hedge_explains():
    """Share = 1.2 x index + noise. The index part has variance
    (1.2 * 1.0%)^2 and the noise (0.6%)^2, so a perfect hedge removes
    144 / (144 + 36) = 80%. An out-of-sample one removes slightly less."""
    share, index, _ = world(beta=1.2, noise=0.006)
    b = rolling_ratio(share, index.to_frame(), window=250)
    resid = hedge_residual(share, index.to_frame(), b)
    assert resid.iloc[:250].isna().all()
    removed = variance_removed(share, resid)
    assert removed == pytest.approx(0.80, abs=0.03)
    hindsight = full_sample_ratio(share, index.to_frame())
    best = variance_removed(share, hedge_residual(share, index.to_frame(), hindsight))
    assert best >= removed - 0.01                         # hindsight is the ceiling


def test_a_sector_hedge_adds_what_the_index_misses():
    share, index, sector = world(beta=1.0, sector_beta=0.7, noise=0.004)
    X1, X2 = index.to_frame(), pd.concat([index, sector], axis=1)
    r1 = hedge_residual(share, X1, rolling_ratio(share, X1, 250))
    r2 = hedge_residual(share, X2, rolling_ratio(share, X2, 250))
    assert variance_removed(share, r2) > variance_removed(share, r1) + 0.15


def test_a_fixed_ratio_can_be_applied_too():
    """Hedging one for one, with no estimation, is the naive benchmark."""
    share, index, _ = world(beta=1.2)
    resid = hedge_residual(share, index.to_frame(), {"index": 1.0})
    assert resid.notna().all()
    assert resid.iloc[10] == pytest.approx(share.iloc[10] - index.iloc[10])


def test_autocorrelation_is_zero_for_noise_and_not_for_a_trend():
    noise = pd.Series(RNG.normal(0, 1, 4000))
    assert abs(autocorrelation(noise)) < 0.05
    sticky = noise.rolling(5).mean().dropna()
    assert autocorrelation(sticky) > 0.5


# ---------------------------------------------------------------------------
# 3. The price reader
# ---------------------------------------------------------------------------

def write_prices(tmp_path, rows):
    cols = ["datadate", "tic", "prccd", "prchd", "prcld", "prcod", "cshtrd", "div", "divd",
            "divsp", "ajexdi", "trfd", "curcdd", "exchg"]
    df = pd.DataFrame([[d, "ZZZ.", px, px, px, px, 1000, div, div, "", adj, "", "CAD", 7]
                       for d, px, div, adj in rows], columns=cols)
    p = tmp_path / "prices_ZZZ.csv"
    df.to_csv(p, index=False)
    return p


def test_a_dividend_is_part_of_the_return(tmp_path):
    """The share closes at 100, then goes ex a 2.00 dividend and closes at
    98. The holder has 98 plus 2 in cash: a return of zero, not -2%."""
    p = write_prices(tmp_path, [("2026-01-05", 100.0, "", 1.0), ("2026-01-06", 98.0, 2.0, 1.0),
                                ("2026-01-07", 99.96, "", 1.0)])
    r = load_total_returns(p)
    assert r.index[0] == pd.Timestamp("2026-01-06") and len(r) == 2
    assert r.iloc[0] == pytest.approx(0.0)
    assert r.iloc[1] == pytest.approx(0.02)


def test_a_split_is_not_a_return(tmp_path):
    """Two for one: the price halves and the adjustment factor says so."""
    p = write_prices(tmp_path, [("2026-01-05", 100.0, "", 2.0), ("2026-01-06", 50.5, "", 1.0)])
    assert load_total_returns(p).iloc[0] == pytest.approx(0.01)


def test_rows_dated_in_the_future_and_bad_prices_are_dropped(tmp_path):
    p = write_prices(tmp_path, [("2026-01-05", 100.0, "", 1.0), ("2026-01-06", 101.0, "", 1.0),
                                ("2026-01-07", 0.0, "", 1.0), ("2026-01-08", 102.01, "", 1.0),
                                ("2031-01-01", 500.0, "", 1.0)])
    r = load_total_returns(p, today=date(2026, 11, 11))
    assert list(r.index) == [pd.Timestamp("2026-01-06"), pd.Timestamp("2026-01-08")]
    assert r.iloc[1] == pytest.approx(0.01)               # 101 -> 102.01 across the gap


def test_index_returns(tmp_path):
    p = tmp_path / "index_X.csv"
    pd.DataFrame({"datadate": ["2026-01-05", "2026-01-06", "2030-01-01"],
                  "prccd": [1000.0, 1010.0, 5.0]}).to_csv(p, index=False)
    r = load_index_returns(p, today=date(2026, 11, 11))
    assert len(r) == 1 and r.iloc[0] == pytest.approx(0.01)
