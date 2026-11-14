"""The currency taken out of a New York vol (C3): made-up numbers only."""

import math
from datetime import date

import numpy as np
import pandas as pd
import pytest

from nyvol import (atm_usd_vol, cad_vol, fx_terms, smile_usd_vol,
                   strike_call_delta, usdcad_from_gbp)


def test_the_formula_inverts_the_build_sheets():
    for cad, fx, rho in ((0.20, 0.07, 0.3), (0.35, 0.10, -0.4), (0.15, 0.06, 0.0)):
        usd = math.sqrt(cad ** 2 + fx ** 2 + 2 * rho * cad * fx)
        assert cad_vol(usd, fx, rho) == pytest.approx(cad)


def test_no_currency_means_no_change():
    assert cad_vol(0.25, 0.0, 0.5) == pytest.approx(0.25)


def test_an_impossible_combination_returns_nothing_not_a_guess():
    assert cad_vol(0.05, 0.10, 0.0) is None              # currency alone exceeds the option
    assert cad_vol(float("nan"), 0.1, 0.2) is None
    assert cad_vol(0.2, 0.1, 1.5) is None


def test_usdcad_is_the_ratio_of_the_two_pound_rates():
    fx = pd.DataFrame({"datadate": ["2020-01-02"] * 2, "fromcurd": ["GBP"] * 2,
                       "tocurd": ["CAD", "USD"], "exratd": [1.70, 1.30]})
    assert usdcad_from_gbp(fx).iloc[0] == pytest.approx(1.70 / 1.30)


def test_fx_terms_find_a_planted_correlation():
    rng = np.random.default_rng(5)
    days = pd.bdate_range("2020-01-01", periods=200)
    f = rng.normal(0, 0.004, 200)                          # value of a CAD in USD, log moves
    s = 0.8 * f / 0.004 * 0.01 + rng.normal(0, 0.006, 200)
    usdcad = pd.Series(np.exp(-np.cumsum(f)), index=days)
    share = pd.Series(np.expm1(s), index=days)
    vol, rho = fx_terms(share, usdcad, days[-1].date(), window=150)
    assert rho == pytest.approx(0.8, abs=0.1)
    assert vol == pytest.approx(0.004 * math.sqrt(365 / 1.4), rel=0.2)


def test_fx_terms_use_only_days_up_to_the_day():
    days = pd.bdate_range("2020-01-01", periods=100)
    usdcad = pd.Series(np.linspace(1.3, 1.4, 100), index=days)
    share = pd.Series(np.random.default_rng(1).normal(0, 0.01, 100), index=days)
    share.iloc[50:] = np.nan                               # nothing after day 50
    vol, rho = fx_terms(share, usdcad, days[40].date(), window=20)
    assert math.isfinite(vol) and math.isfinite(rho)
    assert math.isnan(fx_terms(share, usdcad, days[90].date(), window=20)[0])


def skew_surface():
    rows = []
    for days, low, mid, high in ((30, 0.30, 0.20, 0.18), (91, 0.28, 0.22, 0.21)):
        rows += [dict(days=days, delta=-25, cp_flag="P", impl_volatility=low),
                 dict(days=days, delta=50, cp_flag="C", impl_volatility=mid),
                 dict(days=days, delta=-50, cp_flag="P", impl_volatility=mid),
                 dict(days=days, delta=25, cp_flag="C", impl_volatility=high)]
    return pd.DataFrame(rows).assign(date=pd.Timestamp("2020-03-02"))


def test_a_low_strike_reads_the_put_wing_and_a_high_strike_the_call_wing():
    s, d = skew_surface(), date(2020, 3, 2)
    assert smile_usd_vol(s, d, 30, 0.75) == pytest.approx(0.30)
    assert smile_usd_vol(s, d, 30, 0.50) == pytest.approx(0.20)
    assert smile_usd_vol(s, d, 30, 0.25) == pytest.approx(0.18)
    assert smile_usd_vol(s, d, 30, 0.625) == pytest.approx(0.25)     # halfway down the put wing
    assert smile_usd_vol(s, d, 30, 0.95) == pytest.approx(0.30)      # flat beyond the wing
    assert smile_usd_vol(s, d, 60.5, 0.75) == pytest.approx(0.29)    # between maturities


def test_at_the_money_ignores_the_wings():
    assert atm_usd_vol(skew_surface(), date(2020, 3, 2), 30) == pytest.approx(0.20)


def test_a_strike_slides_along_the_smile_as_the_share_moves():
    at = strike_call_delta(100.0, 100.0, 0.25, 0.20)
    assert at == pytest.approx(0.52, abs=0.01)                     # a touch over half
    assert strike_call_delta(80.0, 100.0, 0.25, 0.20) < 0.05       # share fell: now a high strike
    assert strike_call_delta(120.0, 100.0, 0.25, 0.20) > 0.95
    assert math.isnan(strike_call_delta(100.0, 100.0, 0.0, 0.2))


def test_a_missing_wing_gives_nothing():
    s = skew_surface()
    assert math.isnan(smile_usd_vol(s[s["delta"] != 25], date(2020, 3, 2), 30, 0.5))


def test_atm_vol_reads_between_maturities_and_averages_call_and_put():
    s = pd.DataFrame({"date": pd.Timestamp("2020-03-02"), "days": [30, 30, 91, 91],
                      "impl_volatility": [0.20, 0.22, 0.30, 0.30]})
    assert atm_usd_vol(s, date(2020, 3, 2), 30) == pytest.approx(0.21)
    assert atm_usd_vol(s, date(2020, 3, 2), 60.5) == pytest.approx(0.255)
    assert atm_usd_vol(s, date(2020, 3, 2), 400) == pytest.approx(0.30)     # flat beyond
    assert math.isnan(atm_usd_vol(s, date(2020, 3, 3), 30))
