"""
test_loader_optionm.py: the OptionMetrics reader (build sheet B8), on made-up
files that only mimic the column layout. Nothing here is vendor data.
"""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from loaders.optionm import (load_optionm_curve, load_optionm_dividends,
                             load_optionm_options, load_optionm_prices)

OPT_COLS = ["secid", "date", "symbol", "symbol_flag", "exdate", "last_date", "cp_flag",
            "strike_price", "best_bid", "best_offer", "volume", "open_interest",
            "impl_volatility", "delta", "gamma", "vega", "theta", "optionid", "cfadj",
            "am_settlement", "contract_size", "ss_flag", "forward_price",
            "expiry_indicator", "root", "suffix"]


def opt(day, expiry, cp, strike, bid, ask, volume=0, iv=0.20):
    return [1, day, "ZZZ", 1, expiry, "", cp, strike * 1000, bid, ask, volume, 10, iv,
            0.5, 0.01, 0.1, -0.01, 99, 1.0, 0, 100, 0, "", "", "", ""]


@pytest.fixture
def files(tmp_path):
    rows = [opt("2025-06-02", "2025-07-18", "C", 50.0, 2.00, 2.10, volume=5),
            opt("2025-06-02", "2025-07-18", "P", 50.0, 1.80, 1.90),
            opt("2025-06-02", "2025-07-18", "C", 70.0, 0.00, 0.05, iv=""),
            opt("2025-06-02", "2025-07-18", "P", 30.0, 0.00, 0.00, iv="")]
    pd.DataFrame(rows, columns=OPT_COLS).to_csv(tmp_path / "opprcd_ZZZ_x.csv", index=False)
    pd.DataFrame({"secid": 1, "date": ["2025-06-02", "2025-06-03"], "low": 49, "high": 51,
                  "close": [50.0, 50.5], "volume": 1000, "return": 0.0, "cfadj": 4.0,
                  "open": 50, "cfret": 1.0, "shrout": 1}).to_csv(
        tmp_path / "secprd_ZZZ_x.csv", index=False)
    pd.DataFrame({"date": ["2025-06-02"] * 3 + ["2025-06-03"] * 3,
                  "days": [10, 30, 365] * 2,
                  "rate": [4.0, 4.2, 4.6, 4.1, 4.3, 4.7]}).to_csv(
        tmp_path / "zerocd_x.csv", index=False)
    pd.DataFrame({
        "secid": 1,
        "record_date": ["2025-04-24", "2025-07-24", "2025-07-30", "2025-10-27"],
        "seq_num": 1,
        "ex_date": ["2025-04-24", "2025-07-24", "2025-07-30", "2025-10-27"],
        "amount": [1.00, 1.10, 2.0, 1.10],
        "adj_factor": 1.0, "declare_date": "2025-02-27", "payment_date": "2025-08-22",
        "link_secid": 0, "distr_type": [1, 1, 2, 1], "frequency": 3.0,
        "currency": ["CAD", "CAD", "CAD", "USD"], "approx_flag": 0,
        "cancel_flag": [0, 0, 0, 0], "liquid_flag": 0}).to_csv(
        tmp_path / "distrd_ZZZ.csv", index=False)
    return tmp_path


def test_options_come_back_in_the_same_shape_as_the_exchange_loader(files):
    o = load_optionm_options(files / "opprcd_ZZZ_x.csv", "ZZZ:US")
    assert {"date", "underlying", "expiry", "strike", "call_put", "bid", "ask", "settle",
            "volume", "open_interest", "traded", "two_sided", "tier", "mark",
            "mark_source", "om_iv"} <= set(o.columns)
    a = o[(o["strike"] == 50.0) & (o["call_put"] == "C")].iloc[0]
    assert a["underlying"] == "ZZZ:US" and a["expiry"] == pd.Timestamp("2025-07-18")
    assert a["mark"] == pytest.approx(2.05) and a["tier"] == "traded"
    assert a["om_iv"] == pytest.approx(0.20)


def test_strikes_are_stored_times_a_thousand(files):
    o = load_optionm_options(files / "opprcd_ZZZ_x.csv", "ZZZ:US")
    assert sorted(o["strike"].unique()) == [30.0, 50.0, 70.0]


def test_a_row_without_a_two_sided_quote_has_no_mark(files):
    """Their file has no settlement price to fall back on, so no quote means
    no price, and it is counted, not filled."""
    o = load_optionm_options(files / "opprcd_ZZZ_x.csv", "ZZZ:US")
    far = o[o["strike"].isin([30.0, 70.0])]
    assert far["mark"].isna().all() and (far["tier"] == "no_price").all()
    assert far["om_iv"].isna().all()
    quoted = o[(o["strike"] == 50.0) & (o["call_put"] == "P")].iloc[0]
    assert quoted["tier"] == "quoted"


def test_prices_and_curve(files):
    px = load_optionm_prices(files / "secprd_ZZZ_x.csv")
    assert px[date(2025, 6, 3)] == pytest.approx(50.5)
    c = load_optionm_curve(files / "zerocd_x.csv", date(2025, 6, 2))
    assert c.source == "OptionMetrics zero curve"
    assert c.rate(30 / 365) == pytest.approx(0.042)
    assert c.rate(5 / 365) == pytest.approx(0.040)            # flat before the first point
    assert 0.042 < c.rate(100 / 365) < 0.046
    with pytest.raises(ValueError):
        load_optionm_curve(files / "zerocd_x.csv", date(2025, 6, 10))


def test_dividends_are_cash_only_and_converted_to_the_option_currency(files):
    """The option is in US dollars and the dividend is declared in Canadian
    dollars. A holder of the New York share receives the US dollar value, so
    that is what comes off the share price there. Splits are not dividends."""
    fx = {date(2025, 4, 24): 1.40, date(2025, 7, 24): 1.375}       # CAD per USD
    d = load_optionm_dividends(files / "distrd_ZZZ.csv", fx)
    assert [x[0] for x in d] == [date(2025, 4, 24), date(2025, 7, 24), date(2025, 10, 27)]
    assert d[0][1] == pytest.approx(1.00 / 1.40)
    assert d[1][1] == pytest.approx(1.10 / 1.375)
    assert d[2][1] == pytest.approx(1.10)                          # already in USD
    raw = load_optionm_dividends(files / "distrd_ZZZ.csv", None)
    assert raw[1][1] == pytest.approx(1.10)                        # taken at face value


def test_a_missing_exchange_rate_raises(files):
    with pytest.raises(ValueError, match="exchange rate"):
        load_optionm_dividends(files / "distrd_ZZZ.csv", {date(2025, 4, 24): 1.40})


def test_old_dividends_can_be_left_out_so_they_need_no_rate(files):
    d = load_optionm_dividends(files / "distrd_ZZZ.csv", {date(2025, 7, 24): 1.375},
                               since=date(2025, 6, 1))
    assert [x[0] for x in d] == [date(2025, 7, 24), date(2025, 10, 27)]
