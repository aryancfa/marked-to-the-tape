"""
test_tradingcal.py: build sheet A1.

Every date here is a public holiday or a weekday computed by hand. No market
data is involved.
"""

from datetime import date

import pytest

from tradingcal import (NYSE, TSX, holidays, is_monthly_expiry, is_trading_day,
                        missing_days,
                        shared_trading_days, stale_days, trading_days,
                        unexpected_days, venue_of, year_fraction)

Y26 = (date(2026, 1, 1), date(2026, 12, 31))


# ---------------------------------------------------------------------------
# 1. The two holidays the build sheet names
# ---------------------------------------------------------------------------

def test_family_day_and_civic_holiday_are_excluded():
    """Toronto is shut and New York is open on both, so a naive join would
    record a zero Canadian return against a real American one."""
    family, civic = date(2026, 2, 16), date(2026, 8, 3)
    shared = set(shared_trading_days(["RY", "RY:US"], *Y26))
    tsx = set(trading_days(TSX, *Y26))
    for d in (family, civic):
        assert d not in tsx and d not in shared
    assert is_trading_day(NYSE, civic)          # New York trades that day
    # Family Day falls on Washington's Birthday, so New York is shut too
    assert not is_trading_day(NYSE, family)


def test_shared_days_drop_holidays_from_both_sides():
    shared = set(shared_trading_days(["XIU", "CNQ:US"], *Y26))
    assert date(2026, 11, 26) not in shared     # US Thanksgiving
    assert date(2026, 10, 12) not in shared     # Canadian Thanksgiving
    assert date(2026, 11, 26) in set(trading_days(TSX, *Y26))
    assert date(2026, 10, 12) in set(trading_days(NYSE, *Y26))


def test_one_venue_list_is_just_that_venue():
    assert shared_trading_days(["RY", "XIU", "SXF"], *Y26) == trading_days(TSX, *Y26)


# ---------------------------------------------------------------------------
# 2. Holiday rules, checked against known dates
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("d", [
    date(2025, 4, 18), date(2026, 4, 3), date(2020, 4, 10),   # Good Friday
])
def test_good_friday_shuts_both(d):
    assert not is_trading_day(TSX, d) and not is_trading_day(NYSE, d)


def test_victoria_day_is_the_monday_before_may_25():
    assert date(2026, 5, 18) in holidays(TSX, 2026)
    assert date(2020, 5, 18) in holidays(TSX, 2020)
    assert date(2015, 5, 18) in holidays(TSX, 2015)


def test_weekend_holidays_move_the_right_way():
    # Canada Day 2023 was a Saturday: Toronto took the Monday
    assert date(2023, 7, 3) in holidays(TSX, 2023)
    # Christmas 2021 was a Saturday: Toronto shut Monday and Tuesday,
    # New York shut the Friday before
    assert {date(2021, 12, 27), date(2021, 12, 28)} <= holidays(TSX, 2021)
    assert date(2021, 12, 24) in holidays(NYSE, 2021)
    # New Year's Day 2022 was a Saturday: New York took no day off,
    # Toronto took the Monday
    assert is_trading_day(NYSE, date(2021, 12, 31))
    assert is_trading_day(NYSE, date(2022, 1, 3))
    assert not is_trading_day(TSX, date(2022, 1, 3))


def test_juneteenth_starts_in_2022():
    assert is_trading_day(NYSE, date(2021, 6, 18))
    assert not is_trading_day(NYSE, date(2022, 6, 20))    # Sunday -> Monday
    assert is_trading_day(TSX, date(2022, 6, 20))


def test_new_york_trading_day_counts():
    """Published counts: 250 in 2023, 252 in 2024, 250 in 2025 (which
    includes the one-off closure on 9 January)."""
    for year, n in ((2023, 250), (2024, 252), (2025, 250)):
        assert len(trading_days(NYSE, date(year, 1, 1), date(year, 12, 31))) == n


def test_weekends_never_trade():
    assert all(d.weekday() < 5 for d in trading_days(TSX, *Y26))


def test_monthly_expiry_is_the_third_friday():
    assert is_monthly_expiry(date(2026, 11, 20))
    assert is_monthly_expiry(date(2026, 12, 18))
    assert not is_monthly_expiry(date(2026, 11, 13))      # a weekly
    assert not is_monthly_expiry(date(2026, 11, 27))
    # April 2025: the third Friday was Good Friday, so expiry moved to Thursday
    assert is_monthly_expiry(date(2025, 4, 17))
    assert not is_monthly_expiry(date(2026, 4, 16))       # ordinary Thursday


# ---------------------------------------------------------------------------
# 3. Refusing to guess
# ---------------------------------------------------------------------------

def test_years_before_the_rules_are_verified_raise():
    with pytest.raises(ValueError):
        trading_days(TSX, date(2007, 12, 1), date(2007, 12, 31))


def test_unknown_instrument_raises():
    with pytest.raises(ValueError):
        venue_of("AAPL")


# ---------------------------------------------------------------------------
# 4. Stale and missing days
# ---------------------------------------------------------------------------

def test_stale_days_per_series():
    stale = stale_days(["RY", "RY:US"], date(2026, 7, 27), date(2026, 8, 7))
    assert stale["RY"] == [date(2026, 8, 3)]    # Civic Holiday
    assert stale["RY:US"] == []


def test_missing_and_unexpected_days():
    start, end = date(2026, 8, 3), date(2026, 8, 7)      # Monday is a holiday
    expected = trading_days(TSX, start, end)
    assert len(expected) == 4
    observed = [d for d in expected if d != date(2026, 8, 5)]
    assert missing_days("RY", observed, start, end) == [date(2026, 8, 5)]
    assert unexpected_days("RY", observed) == []
    assert unexpected_days("RY", observed + [date(2026, 8, 3)]) == [date(2026, 8, 3)]


# ---------------------------------------------------------------------------
# 5. The one date-to-time rule
# ---------------------------------------------------------------------------

def test_year_fraction_is_calendar_days_over_365():
    assert year_fraction(date(2026, 11, 9), date(2026, 12, 18)) == 39 / 365
    assert year_fraction(date(2026, 11, 9), date(2026, 11, 9)) == 0.0
    assert year_fraction(date(2026, 11, 9), date(2027, 11, 9)) == 1.0
    assert year_fraction(date(2026, 11, 9), date(2026, 11, 6)) < 0.0
