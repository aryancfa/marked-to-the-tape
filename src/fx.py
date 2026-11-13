"""
fx.py — one exchange rate, for one job: a dividend declared in US dollars on
a share whose options are in Canadian dollars (Barrick; A4).

This is not an FX model. A US-dollar dividend lowers the Toronto share price
by the amount times the exchange rate on the day. Until then the rate is not
known, and the best single guess of a future rate is close to today's (the
forward differs from spot by the interest-rate gap, about a fifth of a cent
per US dollar a quarter, far inside what a dividend projection is good to).
So a future US-dollar dividend is valued at the rate on the valuation day,
and the rate is re-read every day. That makes the schedule point-in-time in
the currency as well: last year's rate never leaks into this year's price.

The rate is the Bank of Canada's daily average (series FXUSDCAD, Canadian
dollars per US dollar), kept under data/raw/fx/ in the Bank's own file
layout.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from rates import read_boc_series

USDCAD = "FXUSDCAD.csv"
MAX_STALE_DAYS = 5          # a long weekend; beyond that the file needs refreshing


def load_usdcad(path: str | Path) -> dict[date, float]:
    """{date: Canadian dollars per US dollar}. read_boc_series is written for
    rates in percent and divides by 100, so that is undone here."""
    return {d: round(v * 100.0, 6) for d, v in read_boc_series(path).items()}


def rate_on(series: dict[date, float], day: date,
            max_stale_days: int = MAX_STALE_DAYS) -> float:
    """The latest rate on or before `day`. Raises if there is none within
    `max_stale_days`: a stale rate is never used silently (hard rule 4)."""
    for back in range(max_stale_days + 1):
        hit = series.get(day - timedelta(days=back))
        if hit is not None:
            return hit
    raise ValueError(f"no USD/CAD rate within {max_stale_days} days of {day}")
