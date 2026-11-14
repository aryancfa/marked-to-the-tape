"""A stress over several days, with the carry kept apart (B6): made-up inputs."""

from datetime import date

import pytest

from book import Position
from horizon import carry_parts, end_of_horizon, share_parts, shocked
from replay import _price

DAY = date(2026, 11, 2)                       # a Monday
RATE = lambda T: 0.03                         # noqa: E731


def option(cp="C", K=100.0, expiry=date(2027, 1, 15)):
    return Position(kind="option", underlying="AAA", call_put=cp, strike=K, expiry=expiry,
                    quantity=1, multiplier=100, venue="MX")


def test_the_horizon_counts_trading_sessions():
    assert end_of_horizon(DAY, 0) == DAY
    assert end_of_horizon(DAY, 5) == date(2026, 11, 9)     # over a weekend
    with pytest.raises(ValueError):
        end_of_horizon(DAY, -1)


def test_the_parts_add_up_to_the_aged_value_exactly():
    pos, end = option(), end_of_horizon(DAY, 5)
    divs = [(date(2026, 11, 5), 1.0), (date(2026, 12, 20), 1.0)]
    c = carry_parts(pos, DAY, end, 100.0, RATE, divs, 0.25)
    assert c["decay"] + c["funding"] + c["dividends"] == pytest.approx(c["aged"] - c["now"],
                                                                         abs=1e-12)
    assert c["expected_spot"] == 99.0


def test_no_ex_date_in_the_window_means_no_dividend_part():
    pos, end = option(), end_of_horizon(DAY, 5)
    c = carry_parts(pos, DAY, end, 100.0, RATE, [(date(2026, 12, 20), 1.0)], 0.25)
    assert c["dividends"] == pytest.approx(0.0, abs=1e-9)


def test_an_ex_date_in_the_window_shows_up_as_dividends():
    pos, end = option(), end_of_horizon(DAY, 5)
    c = carry_parts(pos, DAY, end, 100.0, RATE, [(date(2026, 11, 5), 1.0)], 0.25)
    assert abs(c["dividends"]) > 1e-3


def test_a_long_option_decays_and_a_zero_horizon_carries_nothing():
    pos = option()
    c = carry_parts(pos, DAY, end_of_horizon(DAY, 10), 100.0, RATE, [], 0.25)
    assert c["decay"] < 0
    z = carry_parts(pos, DAY, DAY, 100.0, RATE, [], 0.25)
    assert z["decay"] == z["funding"] == 0.0 and z["aged"] == pytest.approx(z["now"])


def test_no_move_and_the_same_vol_is_no_shock():
    pos, end = option("P"), end_of_horizon(DAY, 5)
    c = carry_parts(pos, DAY, end, 100.0, RATE, [], 0.25)
    assert shocked(pos, end, c["expected_spot"], 0.0, RATE, [], 0.25) == pytest.approx(c["aged"])
    assert shocked(pos, end, c["expected_spot"], -0.1, RATE, [], 0.25) > c["aged"]


def test_the_shock_is_taken_from_the_expected_share_price():
    pos, end = option(), end_of_horizon(DAY, 5)
    divs = [(date(2026, 11, 5), 2.0)]
    c = carry_parts(pos, DAY, end, 100.0, RATE, divs, 0.25)
    s = shocked(pos, end, c["expected_spot"], 0.05, RATE, divs, 0.25)
    assert s == pytest.approx(_price(pos, end, 98.0 * 1.05, RATE, divs, 0.25, 400))


def test_a_horizon_past_expiry_is_refused():
    with pytest.raises(ValueError, match="expiry"):
        carry_parts(option(expiry=date(2026, 11, 6)), DAY, end_of_horizon(DAY, 5), 100.0,
                    RATE, [], 0.25)


def test_a_share_holder_keeps_the_dividend_and_takes_the_move_on_the_expected_price():
    s = share_parts(1000.0, 100.0, [(date(2026, 11, 5), 1.0)], DAY, end_of_horizon(DAY, 5),
                    [-0.2, 0.1])
    assert s["dividends"] == 0.0 and s["decay"] == 0.0
    assert s["shock"] == pytest.approx([1000 * 99.0 * -0.2, 1000 * 99.0 * 0.1])
