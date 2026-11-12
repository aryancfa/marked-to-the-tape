"""
test_loader_ibkr.py: the Interactive Brokers capture layout and its loader
(build sheet SRC). Every price here is made up; none is market data.
"""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from loaders.ibkr import (CAPTURE_COLUMNS, capture_summary, choose_contracts,
                          load_ibkr_options, load_ibkr_underlying, missed_days)
from loaders.mx import OPTION_COLUMNS, UNDERLYING_COLUMNS
from surface import invert_chain

DAY = date(2026, 11, 16)
EXP = "2026-12-18"


def capture(rows, und=(49.95, 50.05, 50.00)):
    out = []
    for strike, cp, bid, ask, last, volume in rows:
        out.append(dict(captured_at="2026-11-16T16:20:00", date=DAY.isoformat(),
                        underlying="ZZZ", expiry=EXP, strike=strike, call_put=cp,
                        bid=bid, ask=ask, last=last, volume=volume,
                        und_bid=und[0], und_ask=und[1], und_last=und[2]))
    return pd.DataFrame(out, columns=CAPTURE_COLUMNS)


ROWS = [(50.0, "C", 1.40, 1.50, 1.45, 12),     # traded and quoted
        (50.0, "P", 1.30, 1.40, -1, 0),        # quoted only; the API's "no value" is -1
        (55.0, "C", -1, -1, 0.20, 0),          # no quote: a stale last is not a price
        (45.0, "P", 0.10, 0.05, -1, 0)]        # crossed


@pytest.fixture
def path(tmp_path):
    p = tmp_path / "20261116" / "quote_ZZZ.csv"
    p.parent.mkdir()
    capture(ROWS).to_csv(p, index=False)
    return p


def test_options_come_back_in_the_exchange_loaders_columns(path):
    o = load_ibkr_options(path)
    assert list(o.columns) == OPTION_COLUMNS
    assert len(o) == 4
    assert o["settle"].isna().all() and o["mx_iv"].isna().all()   # a snapshot has neither


def test_tiers_and_marks_follow_the_quote_and_never_a_stale_last(path):
    o = load_ibkr_options(path).set_index(["strike", "call_put"])
    assert o.loc[(50.0, "C"), "tier"] == "traded"
    assert o.loc[(50.0, "C"), "mark"] == pytest.approx(1.45)
    assert o.loc[(50.0, "P"), "tier"] == "quoted"
    assert o.loc[(50.0, "P"), "mark_source"] == "mid"
    for key in [(55.0, "C"), (45.0, "P")]:                # no quote, and a crossed one
        assert o.loc[key, "tier"] == "no_price"
        assert np.isnan(o.loc[key, "mark"]) and o.loc[key, "mark_source"] == "none"


def test_a_folder_is_searched_through_its_dated_subfolders(path):
    assert len(load_ibkr_options(path.parent.parent)) == 4


def test_underlying_is_the_share_quote_taken_in_the_same_pass(path):
    u = load_ibkr_underlying(path)
    assert list(u.columns) == UNDERLYING_COLUMNS
    assert u["mid"].iloc[0] == pytest.approx(50.0) and u["last"].iloc[0] == pytest.approx(50.0)


def test_two_share_quotes_in_one_days_capture_are_refused(tmp_path):
    df = capture(ROWS)
    df.loc[0, "und_bid"] = 49.0
    p = tmp_path / "quote_ZZZ.csv"
    df.to_csv(p, index=False)
    with pytest.raises(ValueError, match="more than one share quote"):
        load_ibkr_underlying(p)


def test_a_series_captured_twice_on_a_day_is_refused(tmp_path):
    p = tmp_path / "quote_ZZZ.csv"
    pd.concat([capture(ROWS), capture(ROWS[:1])]).to_csv(p, index=False)
    with pytest.raises(ValueError, match="repeat a series"):
        load_ibkr_options(p)


def test_a_file_without_the_capture_columns_is_refused(tmp_path):
    p = tmp_path / "quote_ZZZ.csv"
    capture(ROWS).drop(columns=["und_bid"]).to_csv(p, index=False)
    with pytest.raises(ValueError, match="missing columns"):
        load_ibkr_options(p)


def test_captured_rows_run_through_the_same_inversion_as_exchange_rows(path):
    """The point of matching the columns: nothing downstream changes. Rows
    with no quote get a reason and no vol."""
    inv = invert_chain(load_ibkr_options(path), 50.0, 0.03, [])
    got = inv.set_index(["strike", "call_put"])
    assert got.loc[(50.0, "C"), "reason"] == "ok" and got.loc[(50.0, "C"), "iv"] > 0
    assert got.loc[(55.0, "C"), "reason"] == "no_price_to_invert"
    assert np.isnan(got.loc[(55.0, "C"), "iv"])


def test_contracts_asked_for_are_the_window_the_chart_keeps():
    expiries = [date(2026, 11, 16), date(2026, 11, 20), date(2026, 12, 18), date(2028, 6, 16)]
    strikes = [40.0, 44.0, 48.0, 50.0, 52.0, 56.0, 60.0]
    got = choose_contracts(50.0, expiries, strikes, DAY)
    assert {e for e, _, _ in got} == {date(2026, 11, 20), date(2026, 12, 18)}   # not today, not 2028
    assert {k for _, k, _ in got} == {44.0, 48.0, 50.0, 52.0, 56.0}             # within 12%
    assert len(got) == 2 * 5 * 2
    with pytest.raises(ValueError):
        choose_contracts(float("nan"), expiries, strikes, DAY)


def test_the_capture_log_counts_what_was_asked_returned_and_missed():
    line = capture_summary(DAY, "ZZZ", 6, capture(ROWS), 12.34, note="test")
    assert line == dict(date="2026-11-16", underlying="ZZZ", asked=6, returned=4,
                        two_sided=2, missed=2, seconds=12.3, note="test")


def test_missed_days_lists_trading_days_with_no_capture():
    """Monday 16 to Friday 20 November 2026. ZZZ was captured Monday and
    Wednesday; Tuesday's line returned nothing; YYY was never captured."""
    log = pd.DataFrame([dict(date="2026-11-16", underlying="ZZZ", returned=40),
                        dict(date="2026-11-17", underlying="ZZZ", returned=0),
                        dict(date="2026-11-18", underlying="ZZZ", returned=38)])
    m = missed_days(log, ["ZZZ", "YYY"], date(2026, 11, 16), date(2026, 11, 20))
    zzz = sorted(m.loc[m["underlying"] == "ZZZ", "date"])
    assert zzz == [date(2026, 11, 17), date(2026, 11, 19), date(2026, 11, 20)]
    assert (m["underlying"] == "YYY").sum() == 5
