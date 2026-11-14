"""Named episodes and their joint moves (C3): made-up returns only."""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from episodes import EPISODES, Episode, curve_shift, path, sessions

EP = Episode("t", "test", date(2020, 2, 20), date(2020, 2, 28), "made up")


def returns(cols=("AAA", "BBB"), start="2020-02-01", end="2020-03-31", value=0.01):
    days = pd.bdate_range(start, end)
    return pd.DataFrame({c: value for c in cols}, index=days)


def test_sessions_start_after_the_first_close_and_skip_holidays():
    s = sessions(EP)
    assert s[0] == date(2020, 2, 21) and s[-1] == date(2020, 2, 28)
    assert date(2020, 2, 24) in s and date(2020, 2, 22) not in s
    fam = Episode("f", "f", date(2020, 2, 14), date(2020, 2, 19), "")
    assert date(2020, 2, 17) not in sessions(fam)          # Family Day


def test_moves_compound_from_the_first_close():
    m, missing = path(returns(), EP)
    assert not missing
    assert m["AAA"].iloc[0] == pytest.approx(0.01)
    assert m["AAA"].iloc[-1] == pytest.approx(1.01 ** len(sessions(EP)) - 1)


def test_a_name_missing_a_session_is_left_out_and_named():
    r = returns()
    r.loc["2020-02-25", "BBB"] = np.nan
    m, missing = path(r, EP)
    assert "BBB" not in m and missing == {"BBB": [date(2020, 2, 25)]}


def test_a_name_with_no_history_yet_is_left_out():
    r = returns()
    r.loc[:"2020-02-26", "BBB"] = np.nan                    # listed later
    m, missing = path(r, EP)
    assert list(m.columns) == ["AAA"] and len(missing["BBB"]) == 4


def bills(level_start, level_end, start="2022-03-01", end="2022-06-30"):
    days = pd.bdate_range(start, end)
    path_ = np.linspace(0, 1, len(days))
    return {t: pd.Series(level_start[i] + path_ * (level_end[i] - level_start[i]), index=days)
            for i, t in enumerate((30, 90, 180, 365))}


def test_the_curve_shift_is_the_move_at_each_maturity():
    b = bills([0.002, 0.004, 0.006, 0.010], [0.020, 0.024, 0.028, 0.032])
    f = curve_shift(b, date(2022, 3, 1), date(2022, 6, 30))
    assert f(30 / 365) == pytest.approx(0.018)
    assert f(365 / 365) == pytest.approx(0.022)
    assert f(60 / 365) == pytest.approx(0.019)           # halfway between 30 and 90 days
    assert f(5.0) == pytest.approx(0.022)                 # flat beyond a year
    assert curve_shift(b, date(2022, 3, 1), date(2022, 3, 1))(0.5) == pytest.approx(0.0)


def test_a_missing_or_stale_rate_gives_no_shift_not_a_guess():
    b = bills([0.01] * 4, [0.02] * 4)
    b[90] = b[90][b[90].index <= "2022-04-01"]           # the 3-month series stops
    assert curve_shift(b, date(2022, 3, 1), date(2022, 6, 30)) is None
    assert curve_shift(b, date(2022, 3, 1), date(2022, 4, 4)) is not None   # within 5 days


def test_the_named_episodes_are_in_order_and_have_sessions():
    assert [e.key for e in EPISODES] == ["jan2015", "covid2020", "wti2020", "boc2022"]
    for e in EPISODES:
        assert e.start < e.end and len(sessions(e)) >= 5


def test_the_funding_spread_is_acceptances_less_bills_on_the_same_day():
    from episodes import funding_spread
    days = pd.to_datetime(["2015-01-07", "2015-01-14"])
    ba = pd.Series([0.0130, 0.0140], index=days)
    bills = pd.Series([0.0090, 0.0091, 0.0095], index=pd.to_datetime(["2015-01-06", "2015-01-07", "2015-01-13"]))
    s = funding_spread(ba, bills)
    assert s.iloc[0] == pytest.approx(0.0039)          # same-day bill
    assert s.iloc[1] == pytest.approx(0.0045)          # the session before


def test_the_funding_move_needs_a_recent_reading_at_both_ends():
    from episodes import funding_move
    s = pd.Series([0.003, 0.005], index=pd.to_datetime(["2015-01-07", "2015-01-28"]))
    assert funding_move(s, date(2015, 1, 7), date(2015, 1, 30)) == pytest.approx(0.002)
    assert funding_move(s, date(2015, 1, 7), date(2015, 3, 30)) is None   # stale: nothing
    assert funding_move(s, date(2014, 12, 1), date(2015, 1, 30)) is None  # before it starts
