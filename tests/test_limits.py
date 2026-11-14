"""Limits and usage (C8): made-up numbers only."""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from limits import (LOSS, band, greek_es, limit_from, mapped_delta, move_size, usage,
                    vol_changes_by_bucket)


def test_a_limit_loses_the_tolerance_on_a_one_in_a_hundred_move():
    rng = np.random.default_rng(3)
    r = rng.normal(0, 0.01, 5000)
    m = move_size(r)
    assert m == pytest.approx(0.0258, rel=0.05)              # 2.58 st. dev. for |normal| at 99%
    assert limit_from(m) * m == pytest.approx(LOSS)


def test_too_few_moves_give_no_limit_not_a_guess():
    assert np.isnan(move_size([0.01] * 10))
    assert np.isnan(limit_from(float("nan")))
    assert band(usage(1.0, float("nan"))) == "no limit"


def test_usage_bands():
    assert band(usage(-70.0, 100.0)) == "green"
    assert band(usage(80.0, 100.0)) == "amber"
    assert band(usage(-101.0, 100.0)) == "breach"


def test_mapped_delta_weights_each_name_by_its_beta():
    assert mapped_delta({"A": 100.0, "B": -50.0, "C": 10.0},
                        {"A": 0.8, "B": 1.2, "C": float("nan")}) == pytest.approx(80 - 60)


def test_greek_es_of_a_pure_delta_book_is_the_average_tail_loss():
    r = pd.DataFrame({"A": np.linspace(-0.04, 0.035, 80)})
    es = greek_es({"A": 1_000_000.0}, {}, {}, r)
    worst = np.sort(1_000_000.0 * r["A"].to_numpy())[:2]          # 2.5% of 80 days
    assert es == pytest.approx(-worst.mean())


def test_short_gamma_makes_both_tails_hurt():
    r = pd.DataFrame({"A": np.r_[np.full(40, -0.03), np.full(40, 0.03)]})
    flat = greek_es({"A": 0.0}, {"A": -1_000_000.0}, {}, r)
    assert flat == pytest.approx(900.0)                          # 1e6 x 0.03^2 either way


def test_long_vega_loses_when_a_rise_crushes_vol():
    r = pd.DataFrame({"A": np.r_[np.full(78, 0.0), [0.02, 0.02]]})
    es = greek_es({"A": 0.0}, {}, {"A": 1000.0}, r, vol_beta=-0.5)
    assert es == pytest.approx(1000.0)                           # vol -1 point on a +2% day


def test_vol_changes_are_bucketed_on_their_own_day_and_skip_gaps():
    days = pd.to_datetime(["2026-06-01", "2026-06-02", "2026-06-20"])
    atm = pd.DataFrame({"day": days, "name": "A", "expiry": date(2026, 9, 18),
                        "iv": [20.0, 21.5, 19.0]})
    out = vol_changes_by_bucket(atm, lambda d, e: "short" if (e - d).days < 100 else "long")
    assert list(out["change"]) == [1.5]                          # the 18-day gap is dropped
    assert list(out["bucket"]) == ["long"]
