"""The WCS discount factor (C3): made-up prices and returns only."""

import json

import numpy as np
import pandas as pd
import pytest

from oilfactor import (FACTORS, fit, load_alberta_oil, monthly_average_returns, oil_factors,
                       widening_cases)


def test_the_service_file_is_read_with_its_padded_column_name(tmp_path):
    rows = [{"Date": "2018-01-01T00:00:00", "Type ": "WTI", "Unit": "$US/bbl", "Value": 60.0},
            {"Date": "2018-01-01T00:00:00", "Type ": "WCS", "Unit": "$US/bbl", "Value": 40.0},
            {"Date": "2018-02-01T00:00:00", "Type ": "WTI", "Unit": "$US/bbl", "Value": 61.0},
            {"Date": "2018-02-01T00:00:00", "Type ": "WCS", "Unit": "$US/bbl", "Value": None}]
    p = tmp_path / "oil.json"
    p.write_text(json.dumps(rows))
    oil = load_alberta_oil(p)
    assert list(oil.index.astype(str)) == ["2018-01"]           # February lacks WCS
    assert oil.loc[pd.Period("2018-01"), "WCS"] == 40.0


def test_monthly_averages_follow_the_level():
    days = pd.bdate_range("2020-01-01", "2020-03-31")
    d = pd.DataFrame({"A": 0.0}, index=days)
    d.loc["2020-02-03", "A"] = 0.10                              # one jump early in February
    m = monthly_average_returns(d)
    assert m.loc[pd.Period("2020-02"), "A"] == pytest.approx(np.log(1.10), rel=0.02)
    assert m.loc[pd.Period("2020-03"), "A"] == pytest.approx(np.log(1.10) - m.loc[
        pd.Period("2020-02"), "A"], abs=1e-9)


def made_up(n=120, seed=2):
    rng = np.random.default_rng(seed)
    idx = pd.period_range("2014-01", periods=n, freq="M")
    X = pd.DataFrame({"index": rng.normal(0, 0.03, n), "wti": rng.normal(0, 0.08, n),
                      "discount": rng.normal(0, 0.06, n)}, index=idx)
    y = 1.2 * X["index"] + 0.3 * X["wti"] + 0.5 * X["discount"] + rng.normal(0, 0.01, n)
    return X, y.rename("AAA")


def test_the_fit_recovers_planted_sensitivities():
    X, y = made_up()
    f = fit(y, X)
    for k, true in zip(FACTORS, (1.2, 0.3, 0.5)):
        assert f.beta[k] == pytest.approx(true, abs=0.05)
    assert f.t("discount") > 10 and f.months == 120


def test_too_short_a_history_is_an_error():
    X, y = made_up(n=20)
    with pytest.raises(ValueError, match="too few"):
        fit(y, X)


def test_the_flat_wti_case_is_the_worst_widening_among_calm_months():
    idx = pd.period_range("2018-01", periods=4, freq="M")
    f = pd.DataFrame({"index": 0.0, "wti": [0.01, -0.20, 0.02, 0.0],
                      "discount": [-0.05, -0.30, -0.08, 0.04]}, index=idx)
    (label, x), (_, q) = widening_cases(f)
    assert x == -0.08 and label.startswith("March 2018")        # February moved WTI 20%
    assert q <= -0.08


def test_factors_come_from_prices():
    idx = pd.period_range("2018-01", periods=3, freq="M")
    oil = pd.DataFrame({"WTI": [60.0, 60.0, 66.0], "WCS": [45.0, 40.0, 44.0]}, index=idx)
    f = oil_factors(oil, pd.Series(0.0, index=idx))
    assert f.loc[pd.Period("2018-02"), "wti"] == pytest.approx(0.0)
    assert f.loc[pd.Period("2018-02"), "discount"] == pytest.approx(np.log(40 / 60) - np.log(45 / 60))
    assert f.loc[pd.Period("2018-03"), "discount"] == pytest.approx(0.0)  # both up 10%
