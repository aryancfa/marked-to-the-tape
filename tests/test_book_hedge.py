"""
test_book_hedge.py: the hedge rules behind scripts/book_hedge.py, on
hand-made tables. Every number here is invented.
"""

import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import book_hedge as bh

EXP = date(2027, 3, 19)
DAYS = pd.bdate_range("2026-06-01", periods=40)


def test_the_delta_hedge_earns_minus_delta_times_ratio_times_the_hedge_return():
    D = pd.DataFrame({"AAA": [100_000.0, 100_000.0]}, index=DAYS[:2])
    X = pd.DataFrame({"index": [0.01, -0.02], "ZEB": [0.0, 0.01]}, index=DAYS[:2])
    rolling = {"AAA": pd.DataFrame({"index": [0.8, 0.8], "ZEB": [0.5, 0.5]}, index=DAYS[:2])}
    hindsight = {"AAA": {"index": 1.0, "ZEB": 0.0}}
    h = bh.delta_hedge(D, rolling, hindsight, X)
    assert h.iloc[0] == pytest.approx(-100_000 * 0.8 * 0.01)
    assert h.iloc[1] == pytest.approx(-100_000 * (0.8 * -0.02 + 0.5 * 0.01))
    assert bh.delta_hedge(D, rolling, hindsight, X, honest=False).iloc[0] == pytest.approx(-1000)


def test_no_ratio_yet_means_no_hedge_that_day_not_a_guessed_one():
    D = pd.DataFrame({"AAA": [100_000.0, 100_000.0]}, index=DAYS[:2])
    X = pd.DataFrame({"index": [0.01, 0.01]}, index=DAYS[:2])
    rolling = {"AAA": pd.DataFrame({"index": [np.nan, 0.9]}, index=DAYS[:2])}
    h = bh.delta_hedge(D, rolling, {"AAA": {"index": 1.0}}, X)
    assert h.iloc[0] == 0.0 and h.iloc[1] == pytest.approx(-900.0)


def test_the_future_is_hedged_one_for_one_with_the_index():
    D = pd.DataFrame({"SXF": [-200_000.0]}, index=DAYS[:1])
    X = pd.DataFrame({"index": [0.01]}, index=DAYS[:1])
    assert bh.delta_hedge(D, {}, {}, X).iloc[0] == pytest.approx(2000.0)


def atm_table(beta=1.5, noise=0.0, n=40, seed=1):
    rng = np.random.default_rng(seed)
    x = np.cumsum(rng.normal(0, 0.5, n)) + 15.0
    y = 25.0 + beta * (x - x[0]) + rng.normal(0, noise, n)
    return pd.DataFrame(
        [dict(day=d, name=bh.HEDGE_NAME, expiry=EXP, iv=a) for d, a in zip(DAYS[:n], x)]
        + [dict(day=d, name="AAA", expiry=EXP, iv=b) for d, b in zip(DAYS[:n], y)])


def test_vol_beta_is_recovered_and_uses_earlier_days_only():
    b = bh.vol_betas(atm_table(beta=1.5), EXP)
    col = ("AAA", EXP)
    assert b[col].iloc[:bh.MIN_BETA_DAYS].isna().all()          # too few earlier moves
    assert b[col].iloc[-1] == pytest.approx(1.5, abs=1e-9)
    assert b[(bh.HEDGE_NAME, EXP)].iloc[-1] == pytest.approx(1.0)
    # a change on the LAST day cannot move the last day's beta
    t = atm_table(beta=1.5)
    t.loc[(t["name"] == "AAA") & (t["day"] == DAYS[39]), "iv"] += 50.0
    assert bh.vol_betas(t, EXP)[col].iloc[-1] == pytest.approx(1.5, abs=1e-9)


def chain(marks, spot=50.0, strike=50.0, half=0.05, vega=0.10, delta=(0.5, -0.5)):
    """marks: day -> (call mark, put mark). One strike, both sides."""
    rows = []
    for d, (c, p) in marks.items():
        for cp, m, dl in (("C", c, delta[0]), ("P", p, delta[1])):
            rows.append(dict(day=d, strike=strike, call_put=cp, mark=m, tier="quoted",
                             spot=spot, half=half, delta=dl, vega=vega))
    return pd.DataFrame(rows)


def test_the_straddle_is_sized_to_cancel_beta_weighted_vega_and_charged_the_spread():
    d0, d1, d2 = DAYS[:3]
    ch = chain({d0: (2.0, 2.0), d1: (2.3, 2.1), d2: (2.3, 2.1)})
    # carried INTO d1 and d2: 4,000 dollars a vol point on AAA, beta 1.5
    V = pd.DataFrame({("AAA", EXP): [4000.0, 4000.0]}, index=[d1, d2])
    beta = pd.DataFrame({("AAA", EXP): [1.5, 1.5]}, index=[d1, d2])
    out = bh.vega_hedge(V, beta, ch)
    # straddle vega 0.20 a share = 20 dollars a contract; 6,000 to cancel -> short 300
    assert out.loc[d0, "contracts_out"] == -300
    assert out.loc[d0, "cost"] == pytest.approx(300 * 100 * 0.10)       # two legs, half each
    assert out.loc[d1, "contracts_in"] == -300
    assert out.loc[d1, "pnl"] == pytest.approx(-300 * 100 * 0.4)
    assert out.loc[d1, "delta_in"] == pytest.approx(0.0)                # a straddle at the money
    assert out.loc[d1, "cost"] == 0.0                                   # size unchanged
    assert out.loc[d2, "pnl"] == 0.0


def test_no_beta_for_a_vega_held_means_no_hedge_and_a_missing_mark_is_ignored():
    d0, d1 = DAYS[:2]
    ch = chain({d0: (2.0, 2.0), d1: (2.1, 2.1)})
    V = pd.DataFrame({("AAA", EXP): [4000.0], ("BBB", EXP): [1000.0]}, index=[d1])
    beta = pd.DataFrame({("AAA", EXP): [1.5], ("BBB", EXP): [np.nan]}, index=[d1])
    assert bh.vega_hedge(V, beta, ch)["contracts_out"].abs().sum() == 0
    # BBB has no mark that day (no vega row): it is not a reason to stand down
    V.loc[d1, ("BBB", EXP)] = np.nan
    assert bh.vega_hedge(V, beta, ch).loc[d0, "contracts_out"] == -300


def test_the_straddle_is_replaced_when_the_share_has_moved_away():
    d0, d1, d2 = DAYS[:3]
    near = chain({d0: (2.0, 2.0)})
    moved = pd.concat([chain({d1: (5.0, 0.5)}, spot=54.0, strike=50.0),
                       chain({d1: (2.2, 2.2), d2: (2.2, 2.2)}, spot=54.0, strike=54.0)])
    V = pd.DataFrame({("AAA", EXP): [4000.0, 4000.0]}, index=[d1, d2])
    beta = pd.DataFrame({("AAA", EXP): [1.0, 1.0]}, index=[d1, d2])
    out = bh.vega_hedge(V, beta, pd.concat([near, moved]))
    assert out.loc[d1, "restruck"] == 1
    assert out.loc[d1, "pnl"] == pytest.approx(-200 * 100 * 1.5)        # marked at the OLD strike
    assert out.loc[d1, "cost"] == pytest.approx(2 * 200 * 100 * 0.10)   # out of one, into the other
