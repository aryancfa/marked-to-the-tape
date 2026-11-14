"""Crash betas for the stress test: made-up returns only."""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from scenarios import INDEX, crash_betas


def table(n=1000, seed=3):
    rng = np.random.default_rng(seed)
    idx = rng.normal(0.0, 0.01, n)
    calm = idx > np.quantile(idx, 0.05)
    # AAA: beta 0.5 most days but 2.0 on the index's worst days
    aaa = np.where(calm, 0.5 * idx, 2.0 * idx) + rng.normal(0.0, 0.002, n)
    # BBB: moves against the index on calm days, with it on bad days
    bbb = np.where(calm, -0.4 * idx, 1.2 * idx)
    days = pd.bdate_range("2014-01-01", periods=n)
    return pd.DataFrame({INDEX: idx, "AAA": aaa, "BBB": bbb}, index=days)


def test_the_beta_is_read_off_the_worst_days_only():
    c = crash_betas(table())
    assert c.loc["AAA", "crash_beta"] == pytest.approx(2.0, abs=0.05)
    assert c.loc["BBB", "crash_beta"] == pytest.approx(1.2, abs=1e-9)
    assert c.loc["BBB", "fell"] == 1.0
    assert (c["days"] == 50).all()                   # 5% of 1,000 days


def test_a_negative_calm_beta_does_not_carry_into_a_crash():
    t = table()
    calm = np.cov(t["BBB"], t[INDEX])[0, 1] / t[INDEX].var()
    assert calm < 0.1 < crash_betas(t).loc["BBB", "crash_beta"]


def test_the_index_against_itself_is_one():
    t = table()
    t["SAME"] = t[INDEX]
    assert crash_betas(t).loc["SAME", "crash_beta"] == pytest.approx(1.0)


def test_too_few_bad_days_with_a_return_is_an_error_not_a_guess():
    t = table()
    t.loc[t.index[:-1], "AAA"] = np.nan              # one day with a return
    with pytest.raises(ValueError, match="AAA"):
        crash_betas(t)
