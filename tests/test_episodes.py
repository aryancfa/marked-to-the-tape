"""Named episodes and their joint moves (C3): made-up returns only."""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from episodes import EPISODES, Episode, path, sessions

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


def test_the_named_episodes_are_in_order_and_have_sessions():
    assert [e.key for e in EPISODES] == ["jan2015", "covid2020", "wti2020", "boc2022"]
    for e in EPISODES:
        assert e.start < e.end and len(sessions(e)) >= 5
