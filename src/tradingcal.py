"""
tradingcal.py — trading days and the one date-to-time rule (build sheet A1).

Why this exists
---------------
Toronto and New York close on different holidays. Join two price series on
the date alone and every day one market was shut becomes a zero return on
that side, which drags every correlation and hedge ratio toward zero. Nothing
downstream can repair that, so statistics are computed only on days both
markets traded: the SHARED trading days.

The second job is turning dates into the times the pricer uses. There is one
function for it, year_fraction, so time to expiry and time to an ex-dividend
date are always measured the same way. See the convention in voleng.py.

Scope and honesty
-----------------
Holidays are computed from rules, not downloaded, so this runs offline. The
rules are right for 2008 onward (Family Day became a Toronto holiday in 2008)
and a year before that raises instead of guessing. One-off closures (storms,
days of mourning) are listed by hand in _NYSE_SPECIAL; add to it when one
happens. Half days (24 December in Toronto, the day after US Thanksgiving)
count as trading days: there is a close, so there is a return.

"TSX" here also stands for the Montreal Exchange, which keeps the same
holiday schedule for equity and index derivatives.

Not named calendar.py on purpose: src/ is on the import path, and that name
would hide Python's own calendar module.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Iterable, Mapping, Sequence

FIRST_YEAR = 2008

TSX, NYSE = "TSX", "NYSE"

# Instrument -> venue. A bare ticker is the Toronto/Montreal line; the New
# York listing of the same company is written with a ":US" suffix.
_CANADIAN = ("SXF", "SXO", "XIU", "ZEB", "XEG",
             "RY", "TD", "BNS", "BMO", "CM", "CNQ", "SU")
_INTERLISTED = ("RY", "TD", "BNS", "BMO", "CM", "CNQ", "SU")
VENUE: dict[str, str] = {
    **{s: TSX for s in _CANADIAN},
    **{f"{s}:US": NYSE for s in _INTERLISTED},
    TSX: TSX,
    NYSE: NYSE,
}

# One-off full-day New York closures since 2008.
_NYSE_SPECIAL = {
    date(2012, 10, 29), date(2012, 10, 30),   # Hurricane Sandy
    date(2018, 12, 5),                        # day of mourning, G. H. W. Bush
    date(2025, 1, 9),                         # day of mourning, J. Carter
}


# --------------------------------------------------------------------------
# date arithmetic
# --------------------------------------------------------------------------

def year_fraction(start: date, end: date) -> float:
    """
    Calendar days between two dates, divided by 365.

    This is the only place a date becomes a time. Use it for time to expiry
    and for the time to each ex-dividend date, both measured from the
    valuation date. Negative when `end` is before `start`.
    """
    return (end - start).days / 365.0


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """n-th given weekday of a month (Monday=0). n=1 is the first."""
    first = date(year, month, 1)
    return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    nxt = date(year + (month == 12), month % 12 + 1, 1)
    last = nxt - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _easter(year: int) -> date:
    """Easter Sunday, Gregorian calendar (anonymous algorithm)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    m = (32 + 2 * e + 2 * i - h - k) % 7
    n = (a + 11 * h + 22 * m) // 451
    month = (h + m - 7 * n + 114) // 31
    day = (h + m - 7 * n + 114) % 31 + 1
    return date(year, month, day)


def _to_monday(d: date) -> date:
    """Toronto rule: a holiday on Saturday or Sunday is taken the next Monday."""
    return d + timedelta(days={5: 2, 6: 1}.get(d.weekday(), 0))


def _nearest_weekday(d: date) -> date:
    """New York rule: Saturday is taken on Friday, Sunday on Monday."""
    return d + timedelta(days={5: -1, 6: 1}.get(d.weekday(), 0))


def is_monthly_expiry(d: date) -> bool:
    """
    True for the standard monthly option expiry: the third Friday of the
    month, or the Thursday before it when that Friday is a holiday (Good
    Friday can be one).

    Monthlies carry most of the open interest and the tightest quotes; the
    weekly expiries in between are thinner and wider.
    """
    third_friday = _nth_weekday(d.year, d.month, 4, 3)
    if d == third_friday:
        return True
    return (d == third_friday - timedelta(days=1)
            and third_friday in holidays(TSX, d.year))


# --------------------------------------------------------------------------
# holidays
# --------------------------------------------------------------------------

def _tsx_holidays(year: int) -> set[date]:
    xmas = date(year, 12, 25)
    # Christmas and Boxing Day are both taken; when they touch a weekend the
    # pair slides so two weekdays are closed.
    if xmas.weekday() == 4:      # Fri: Boxing Day Saturday -> Monday
        pair = {xmas, xmas + timedelta(days=3)}
    elif xmas.weekday() == 5:    # Sat: Monday and Tuesday
        pair = {xmas + timedelta(days=2), xmas + timedelta(days=3)}
    elif xmas.weekday() == 6:    # Sun: Monday and Tuesday
        pair = {xmas + timedelta(days=1), xmas + timedelta(days=2)}
    else:
        pair = {xmas, xmas + timedelta(days=1)}
    may25 = date(year, 5, 25)
    victoria = may25 - timedelta(days=(may25.weekday() or 7))  # Monday before 25 May
    return pair | {
        _to_monday(date(year, 1, 1)),             # New Year's Day
        _nth_weekday(year, 2, 0, 3),              # Family Day
        _easter(year) - timedelta(days=2),        # Good Friday
        victoria,                                 # Victoria Day
        _to_monday(date(year, 7, 1)),             # Canada Day
        _nth_weekday(year, 8, 0, 1),              # Civic Holiday
        _nth_weekday(year, 9, 0, 1),              # Labour Day
        _nth_weekday(year, 10, 0, 2),             # Thanksgiving
    }


def _nyse_holidays(year: int) -> set[date]:
    out = {
        _nth_weekday(year, 1, 0, 3),              # Martin Luther King Jr. Day
        _nth_weekday(year, 2, 0, 3),              # Washington's Birthday
        _easter(year) - timedelta(days=2),        # Good Friday
        _last_weekday(year, 5, 0),                # Memorial Day
        _nearest_weekday(date(year, 7, 4)),       # Independence Day
        _nth_weekday(year, 9, 0, 1),              # Labor Day
        _nth_weekday(year, 11, 3, 4),             # Thanksgiving
        _nearest_weekday(date(year, 12, 25)),     # Christmas
    }
    # New Year's Day on a Saturday is NOT taken on the Friday before.
    ny = date(year, 1, 1)
    if ny.weekday() != 5:
        out.add(_nearest_weekday(ny))
    if year >= 2022:
        out.add(_nearest_weekday(date(year, 6, 19)))   # Juneteenth
    return out | {d for d in _NYSE_SPECIAL if d.year == year}


def holidays(venue: str, year: int) -> set[date]:
    """Full-day closures of `venue` ("TSX" or "NYSE") that fall on weekdays."""
    if year < FIRST_YEAR:
        raise ValueError(
            f"holiday rules are only verified from {FIRST_YEAR}; got {year}")
    if venue == TSX:
        return _tsx_holidays(year)
    if venue == NYSE:
        return _nyse_holidays(year)
    raise ValueError(f"unknown venue {venue!r}")


def venue_of(instrument: str) -> str:
    """Venue for an instrument or venue name. Unknown names raise."""
    try:
        return VENUE[instrument]
    except KeyError:
        raise ValueError(
            f"unknown instrument {instrument!r}; add it to tradingcal.VENUE"
        ) from None


# --------------------------------------------------------------------------
# trading days
# --------------------------------------------------------------------------

def is_trading_day(venue: str, d: date) -> bool:
    return d.weekday() < 5 and d not in holidays(venue, d.year)


def trading_days(venue: str, start: date, end: date) -> list[date]:
    """Trading days of one venue, both ends included, in order."""
    cache: dict[int, set[date]] = {}
    out, d = [], start
    while d <= end:
        if d.weekday() < 5:
            hol = cache.get(d.year)
            if hol is None:
                hol = cache[d.year] = holidays(venue, d.year)
            if d not in hol:
                out.append(d)
        d += timedelta(days=1)
    return out


def shared_trading_days(instruments: Sequence[str], start: date,
                        end: date) -> list[date]:
    """
    Days on which EVERY listed instrument's venue was open.

    Returns are computed between consecutive shared days, so a day one market
    was shut is skipped on both sides and the next return on each side spans
    the same stretch of time.
    """
    venues = {venue_of(i) for i in instruments}
    if not venues:
        return []
    days = None
    for v in sorted(venues):
        s = set(trading_days(v, start, end))
        days = s if days is None else days & s
    return sorted(days)


def stale_days(instruments: Sequence[str], start: date,
               end: date) -> dict[str, list[date]]:
    """
    Per instrument: days its own venue was shut while another venue in the
    list was open. On these days the series has no new price, and a naive
    join would carry yesterday's forward as a zero return.
    """
    open_by_venue = {v: set(trading_days(v, start, end))
                     for v in {venue_of(i) for i in instruments}}
    any_open = set().union(*open_by_venue.values()) if open_by_venue else set()
    return {i: sorted(any_open - open_by_venue[venue_of(i)])
            for i in instruments}


def missing_days(instrument: str, observed: Iterable[date], start: date,
                 end: date) -> list[date]:
    """
    Trading days of the instrument's venue with no observation in `observed`.

    For the capture log: a gap here is a day the download or capture missed,
    not a holiday.
    """
    have = set(observed)
    return [d for d in trading_days(venue_of(instrument), start, end)
            if d not in have]


def unexpected_days(instrument: str, observed: Iterable[date]) -> list[date]:
    """Observed dates on which the venue should have been shut. Any entry
    means either the data or this calendar is wrong, and should be looked at."""
    v = venue_of(instrument)
    return sorted(d for d in set(observed) if not is_trading_day(v, d))
